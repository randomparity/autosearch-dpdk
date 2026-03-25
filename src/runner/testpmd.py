"""Testpmd execution and throughput measurement."""

from __future__ import annotations

import contextlib
import logging
import os
import pty
import re
import select
import statistics
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

RX_PACKETS_RE = re.compile(r"RX-packets:\s+(\d+)")
RX_PPS_RE = re.compile(r"Rx-pps:\s+(\d+)")


def validate_vdev_config(vdevs: list[str]) -> list[str]:
    """Check for misconfigured vdev strings.

    Returns a list of warning messages (empty if no issues found).
    """
    warnings: list[str] = []
    for vdev in vdevs:
        parts = vdev.split(",")
        has_server = any(p.strip() == "role=server" for p in parts)
        has_zc = any(p.strip() == "zero-copy=yes" for p in parts)
        if has_server and has_zc:
            warnings.append(
                f"vdev '{vdev}': server role ignores zero-copy=yes (only client supports ZC)"
            )
    return warnings


@dataclass
class TestpmdResult:
    """Result of a testpmd throughput measurement."""

    success: bool
    throughput_mpps: float | None
    port_stats: str | None
    error: str | None
    duration_seconds: float
    profile_summary: dict | None = None


def run_testpmd(
    build_dir: Path,
    config: dict,
    timeout: int = 600,
    profile_config: dict | None = None,
) -> TestpmdResult:
    """Run testpmd in io-fwd mode and measure bi-directional throughput.

    Uses a pseudo-TTY so testpmd flushes output line-by-line.

    Args:
        build_dir: Path to the DPDK build directory.
        config: Runner configuration dictionary.
        timeout: Maximum seconds before testpmd is killed.
        profile_config: Optional profiling configuration dict with
            'enabled', 'frequency', 'sudo' keys.

    Returns:
        A TestpmdResult with throughput, raw stats, and optional profile summary.
    """
    start = time.monotonic()
    testpmd_cfg = config.get("testpmd", {})

    testpmd_bin = build_dir / "app" / "dpdk-testpmd"
    if not testpmd_bin.exists():
        return TestpmdResult(
            success=False,
            throughput_mpps=None,
            port_stats=None,
            error=f"testpmd binary not found at {testpmd_bin}",
            duration_seconds=time.monotonic() - start,
        )

    lcores = testpmd_cfg.get("lcores", "4-7")
    pci_addrs = testpmd_cfg.get("pci", [])
    vdevs = testpmd_cfg.get("vdev", [])
    for warning in validate_vdev_config(vdevs):
        logger.warning(warning)
    no_pci = testpmd_cfg.get("no_pci", False)
    extra_eal_args = testpmd_cfg.get("extra_eal_args", [])
    nb_cores = int(testpmd_cfg.get("nb_cores", 2))
    rxq = int(testpmd_cfg.get("rxq", 1))
    txq = int(testpmd_cfg.get("txq", 1))
    rxd = int(testpmd_cfg.get("rxd", 1024))
    txd = int(testpmd_cfg.get("txd", 1024))
    burst = int(testpmd_cfg.get("burst", 32))
    forward_mode = testpmd_cfg.get("forward_mode", "io")
    warmup_seconds = int(testpmd_cfg.get("warmup_seconds", 5))
    measure_seconds = int(testpmd_cfg.get("measure_seconds", 10))

    eal_args = ["-l", lcores]
    for pci in pci_addrs:
        eal_args.extend(["-a", pci])
    for vdev in vdevs:
        eal_args.extend(["--vdev", vdev])
    if no_pci:
        eal_args.append("--no-pci")
    eal_args.extend(extra_eal_args)

    use_sudo = testpmd_cfg.get("sudo", True)
    cmd = [
        *(["sudo"] if use_sudo else []),
        str(testpmd_bin),
        *eal_args,
        "--",
        f"--nb-cores={nb_cores}",
        f"--rxq={rxq}",
        f"--txq={txq}",
        f"--rxd={rxd}",
        f"--txd={txd}",
        f"--burst={burst}",
        "--auto-start",
        "--tx-first",
        f"--forward-mode={forward_mode}",
    ]

    logger.info("Starting testpmd: %s", " ".join(cmd))

    # Use a PTY so testpmd line-buffers its output
    master_fd, slave_fd = pty.openpty()

    try:
        proc = subprocess.Popen(
            cmd,
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            close_fds=True,
        )
        os.close(slave_fd)
    except OSError as exc:
        os.close(master_fd)
        os.close(slave_fd)
        return TestpmdResult(
            success=False,
            throughput_mpps=None,
            port_stats=None,
            error=f"Failed to start testpmd: {exc}",
            duration_seconds=time.monotonic() - start,
        )

    try:
        return _measure_throughput(
            proc,
            master_fd,
            warmup_seconds,
            measure_seconds,
            timeout,
            start,
            profile_config=profile_config,
            lcores=lcores,
        )
    finally:
        _ensure_stopped(proc, master_fd)


def _read_until(fd: int, marker: str, timeout: int) -> str:
    """Read from fd until marker is found or timeout expires."""
    output: list[str] = []
    deadline = time.monotonic() + timeout
    buf = ""

    while time.monotonic() < deadline:
        remaining = max(0.1, deadline - time.monotonic())
        ready, _, _ = select.select([fd], [], [], min(remaining, 1.0))
        if not ready:
            continue
        try:
            chunk = os.read(fd, 4096).decode("utf-8", errors="replace")
        except OSError:
            break
        if not chunk:
            break
        buf += chunk

        # Log complete lines as they arrive
        while "\n" in buf:
            line, buf = buf.split("\n", 1)
            logger.debug("testpmd: %s", line.rstrip())
            output.append(line + "\n")

        joined = "".join(output) + buf
        if marker in joined:
            output.append(buf)
            logger.info("Found marker: %s", marker.strip())
            return "".join(output)

    output.append(buf)
    return "".join(output)


def _measure_throughput(
    proc: subprocess.Popen,
    fd: int,
    warmup: int,
    measure: int,
    timeout: int,
    start: float,
    *,
    profile_config: dict | None = None,
    lcores: str = "0",
) -> TestpmdResult:
    """Wait for testpmd to start, measure, then stop and parse stats."""
    boot_output = _read_until(fd, "Press enter to exit", min(timeout, 60))
    if proc.poll() is not None:
        return TestpmdResult(
            False,
            None,
            boot_output,
            "testpmd exited during startup",
            time.monotonic() - start,
        )

    if "Press enter to exit" not in boot_output:
        return TestpmdResult(
            False,
            None,
            boot_output,
            "testpmd did not reach forwarding state",
            time.monotonic() - start,
        )

    total_time = warmup + measure
    logger.info("Warming up %ds + measuring %ds", warmup, measure)

    # Split sleep: warmup first, then profile during measurement window
    time.sleep(warmup)
    profile_summary = None
    if profile_config and profile_config.get("enabled"):
        profile_summary = _run_profiling(
            proc.pid,
            measure,
            profile_config,
            lcores=lcores,
        )
    else:
        time.sleep(measure)

    logger.info("Stopping testpmd after %ds", total_time)
    os.write(fd, b"\n")

    shutdown_output = _read_until(fd, "Bye...", timeout=30)
    proc.wait(timeout=10)

    all_output = boot_output + shutdown_output

    throughput = _parse_throughput(all_output, total_time)
    if throughput is None:
        return TestpmdResult(
            False,
            None,
            all_output,
            "Failed to parse throughput from stats",
            time.monotonic() - start,
        )

    return TestpmdResult(
        True,
        throughput,
        all_output,
        None,
        time.monotonic() - start,
        profile_summary=profile_summary,
    )


def _find_child_pid(parent_pid: int) -> int | None:
    """Find the first child process of a given PID.

    When testpmd runs under sudo, proc.pid is the sudo process.
    The actual testpmd process is the child of sudo.
    """
    try:
        result = subprocess.run(
            ["pgrep", "-P", str(parent_pid)],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return int(result.stdout.strip().splitlines()[0])
    except (subprocess.TimeoutExpired, ValueError, OSError) as exc:
        logger.debug("Failed to find child of PID %d: %s", parent_pid, exc)
    return None


def _resolve_testpmd_pid(proc_pid: int, use_sudo: bool) -> int:
    """Resolve the actual testpmd PID, traversing sudo if needed."""
    if not use_sudo:
        return proc_pid

    child = _find_child_pid(proc_pid)
    if child is not None:
        logger.info("Resolved testpmd PID: %d (child of sudo %d)", child, proc_pid)
        return child

    logger.warning("Could not find child of sudo (pid=%d), profiling sudo PID", proc_pid)
    return proc_pid


def _run_profiling(
    pid: int,
    duration: int,
    config: dict,
    *,
    lcores: str = "0",
) -> dict | None:
    """Run perf profiling during the measurement window.

    Args:
        pid: testpmd process ID (may be sudo wrapper).
        duration: Measurement duration in seconds.
        config: Profiling config with 'frequency', 'sudo' keys.
        lcores: CPU list string (e.g. "4-12") for system-wide profiling.

    Returns:
        Compact profile summary dict, or None on failure.
    """
    from src.perf.analyze import summarize
    from src.perf.arch import load_arch_profile
    from src.perf.profile import profile_pid

    repo_root = Path(__file__).resolve().parent.parent.parent
    output_dir = repo_root / "perf" / "results" / str(int(time.time()))
    result = profile_pid(
        pid=pid,
        duration=duration,
        output_dir=output_dir,
        frequency=config.get("frequency", 99),
        sudo=config.get("sudo", True),
        cpus=lcores,
    )

    if not result.success:
        logger.warning("Profiling failed: %s", result.error)
        return None

    profile = load_arch_profile()
    return summarize(result.counters, result.folded_stacks, profile)


def _parse_throughput(output: str, duration: float) -> float | None:
    """Parse accumulated forward stats and compute bi-directional Mpps."""
    acc_section = output.split("Accumulated forward statistics for all ports")
    if len(acc_section) >= 2:
        rx_match = RX_PACKETS_RE.search(acc_section[1])
        if rx_match and duration > 0:
            total_rx = int(rx_match.group(1))
            mpps = total_rx / duration / 1_000_000
            logger.info(
                "Throughput: %.2f Mpps (RX-packets=%d over %.0fs)",
                mpps,
                total_rx,
                duration,
            )
            return round(mpps, 4)

    # Fallback: per-port Rx-pps
    matches = RX_PPS_RE.findall(output)
    if matches:
        total_pps = sum(int(m) for m in matches)
        mpps = total_pps / 1_000_000
        logger.info(
            "Throughput: %.2f Mpps (from Rx-pps, per-port: %s)",
            mpps,
            ", ".join(matches),
        )
        return round(mpps, 4)

    logger.warning("No throughput data found in output")
    return None


def _ensure_stopped(proc: subprocess.Popen, fd: int) -> None:
    """Make sure testpmd is fully stopped and close the PTY."""
    if proc.poll() is None:
        try:
            os.write(fd, b"\n")
            proc.wait(timeout=10)
        except (subprocess.TimeoutExpired, OSError):
            logger.warning("testpmd did not exit gracefully, killing")
            proc.kill()
            proc.wait(timeout=5)

    with contextlib.suppress(OSError):
        os.close(fd)


def run_testpmd_repeated(
    build_dir: Path,
    config: dict,
    timeout: int = 600,
    profile_config: dict | None = None,
) -> TestpmdResult:
    """Run testpmd one or more times and return the median result.

    When ``repeat_count`` is 1 (default), delegates directly to
    :func:`run_testpmd` with zero overhead.  For N > 1, runs testpmd
    N times, profiles only the final run, and returns the median
    throughput.

    Args:
        build_dir: Path to the DPDK build directory.
        config: Runner configuration dictionary.
        timeout: Maximum total seconds across all runs.
        profile_config: Optional profiling configuration dict.

    Returns:
        A TestpmdResult with median throughput across all runs.
    """
    repeat_count = int(config.get("testpmd", {}).get("repeat_count", 1))

    if repeat_count <= 1:
        return run_testpmd(build_dir, config, timeout, profile_config)

    per_run_timeout = max(60, timeout // repeat_count)
    results: list[TestpmdResult] = []

    for i in range(repeat_count):
        is_last = i == repeat_count - 1
        run_profile = profile_config if is_last else None
        result = run_testpmd(build_dir, config, per_run_timeout, run_profile)

        if not result.success:
            logger.warning("Run %d/%d failed, aborting", i + 1, repeat_count)
            return result

        logger.info(
            "Run %d/%d: %.4f Mpps",
            i + 1,
            repeat_count,
            result.throughput_mpps or 0,
        )
        results.append(result)

    throughputs = [r.throughput_mpps for r in results if r.throughput_mpps is not None]
    median = statistics.median(throughputs)
    total_duration = sum(r.duration_seconds for r in results)

    logger.info(
        "Median of %d runs: %.4f Mpps (individual: %s)",
        len(throughputs),
        median,
        ", ".join(f"{t:.4f}" for t in throughputs),
    )

    last = results[-1]
    return TestpmdResult(
        success=True,
        throughput_mpps=round(median, 4),
        port_stats=last.port_stats,
        error=None,
        duration_seconds=total_duration,
        profile_summary=last.profile_summary,
    )
