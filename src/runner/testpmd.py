"""Testpmd execution and throughput measurement."""

from __future__ import annotations

import logging
import os
import pty
import re
import select
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

RX_PACKETS_RE = re.compile(r"RX-packets:\s+(\d+)")
RX_PPS_RE = re.compile(r"Rx-pps:\s+(\d+)")


@dataclass
class TestpmdResult:
    """Result of a testpmd throughput measurement."""

    success: bool
    throughput_mpps: float | None
    port_stats: str | None
    error: str | None
    duration_seconds: float


def run_testpmd(
    build_dir: Path,
    config: dict,
    timeout: int = 600,
) -> TestpmdResult:
    """Run testpmd in io-fwd mode and measure bi-directional throughput.

    Uses a pseudo-TTY so testpmd flushes output line-by-line.

    Args:
        build_dir: Path to the DPDK build directory.
        config: Runner configuration dictionary.
        timeout: Maximum seconds before testpmd is killed.

    Returns:
        A TestpmdResult with throughput and raw stats.
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
    pci_addrs = testpmd_cfg.get("pci", ["01:00.0", "01:00.1"])
    nb_cores = int(testpmd_cfg.get("nb_cores", 2))
    rxq = int(testpmd_cfg.get("rxq", 1))
    txq = int(testpmd_cfg.get("txq", 1))
    rxd = int(testpmd_cfg.get("rxd", 1024))
    txd = int(testpmd_cfg.get("txd", 1024))
    warmup_seconds = int(testpmd_cfg.get("warmup_seconds", 5))
    measure_seconds = int(testpmd_cfg.get("measure_seconds", 10))

    eal_args = ["-l", lcores]
    for pci in pci_addrs:
        eal_args.extend(["-a", pci])

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
        "--auto-start",
        "--tx-first",
        "--forward-mode=io",
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
        result = _measure_throughput(
            proc, master_fd, warmup_seconds, measure_seconds, timeout
        )
        return TestpmdResult(
            success=result[0],
            throughput_mpps=result[1],
            port_stats=result[2],
            error=result[3],
            duration_seconds=time.monotonic() - start,
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
) -> tuple[bool, float | None, str | None, str | None]:
    """Wait for testpmd to start, measure, then stop and parse stats."""
    boot_output = _read_until(fd, "Press enter to exit", min(timeout, 60))
    if proc.poll() is not None:
        return (False, None, boot_output, "testpmd exited during startup")

    if "Press enter to exit" not in boot_output:
        return (
            False, None, boot_output,
            "testpmd did not reach forwarding state",
        )

    total_time = warmup + measure
    logger.info("Warming up %ds + measuring %ds", warmup, measure)
    time.sleep(total_time)

    # Press Enter to stop testpmd
    logger.info("Stopping testpmd after %ds", total_time)
    os.write(fd, b"\n")

    # Read shutdown output with forward stats
    shutdown_output = _read_until(fd, "Bye...", timeout=30)
    proc.wait(timeout=10)

    all_output = boot_output + shutdown_output

    throughput = _parse_throughput(all_output, total_time)
    if throughput is None:
        return (
            False, None, all_output,
            "Failed to parse throughput from stats",
        )

    return (True, throughput, all_output, None)


def _parse_throughput(output: str, duration: float) -> float | None:
    """Parse accumulated forward stats and compute bi-directional Mpps."""
    acc_section = output.split(
        "Accumulated forward statistics for all ports"
    )
    if len(acc_section) >= 2:
        rx_match = RX_PACKETS_RE.search(acc_section[1])
        if rx_match and duration > 0:
            total_rx = int(rx_match.group(1))
            mpps = total_rx / duration / 1_000_000
            logger.info(
                "Throughput: %.2f Mpps (RX-packets=%d over %.0fs)",
                mpps, total_rx, duration,
            )
            return round(mpps, 4)

    # Fallback: per-port Rx-pps
    matches = RX_PPS_RE.findall(output)
    if matches:
        total_pps = sum(int(m) for m in matches)
        mpps = total_pps / 1_000_000
        logger.info(
            "Throughput: %.2f Mpps (from Rx-pps, per-port: %s)",
            mpps, ", ".join(matches),
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

    import contextlib

    with contextlib.suppress(OSError):
        os.close(fd)
