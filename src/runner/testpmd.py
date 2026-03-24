"""Testpmd execution and throughput measurement."""

from __future__ import annotations

import logging
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

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

    Launches testpmd with tx_first, waits for warmup, then samples
    port stats over a measurement window to compute Mpps.

    Args:
        build_dir: Path to the DPDK build directory (contains app/dpdk-testpmd).
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

    cmd = [
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

    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
    except OSError as exc:
        return TestpmdResult(
            success=False,
            throughput_mpps=None,
            port_stats=None,
            error=f"Failed to start testpmd: {exc}",
            duration_seconds=time.monotonic() - start,
        )

    try:
        result = _measure_throughput(
            proc, warmup_seconds, measure_seconds, timeout
        )
        return TestpmdResult(
            success=result[0],
            throughput_mpps=result[1],
            port_stats=result[2],
            error=result[3],
            duration_seconds=time.monotonic() - start,
        )
    finally:
        _stop_testpmd(proc)


def _wait_for_prompt(proc: subprocess.Popen, timeout: int) -> str:
    """Read testpmd output until we see the testpmd> prompt."""
    output: list[str] = []
    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        line = proc.stdout.readline()
        if not line:
            break
        output.append(line)
        logger.debug("testpmd: %s", line.rstrip())
        if "testpmd>" in line:
            return "".join(output)

    return "".join(output)


def _send_command(proc: subprocess.Popen, command: str) -> None:
    """Send a command to testpmd's stdin."""
    logger.debug("testpmd cmd: %s", command)
    proc.stdin.write(command + "\n")
    proc.stdin.flush()


def _measure_throughput(
    proc: subprocess.Popen,
    warmup: int,
    measure: int,
    timeout: int,
) -> tuple[bool, float | None, str | None, str | None]:
    """Drive testpmd through warmup and measurement, return results.

    Returns:
        (success, throughput_mpps, port_stats_text, error_message)
    """
    boot_output = _wait_for_prompt(proc, timeout=min(timeout, 60))
    if proc.poll() is not None:
        return (False, None, boot_output, "testpmd exited during startup")

    logger.info("Warming up for %ds", warmup)
    time.sleep(warmup)

    # Reset counters
    _send_command(proc, "show port stats all")
    _wait_for_prompt(proc, timeout=10)

    logger.info("Measuring for %ds", measure)
    time.sleep(measure)

    # Collect measurement
    _send_command(proc, "show port stats all")
    stats_output = _wait_for_prompt(proc, timeout=10)

    throughput = _parse_throughput(stats_output)
    if throughput is None:
        return (False, None, stats_output, "Failed to parse Rx-pps from stats")

    return (True, throughput, stats_output, None)


def _parse_throughput(stats_output: str) -> float | None:
    """Parse Rx-pps from all ports and return total bi-directional Mpps."""
    matches = RX_PPS_RE.findall(stats_output)
    if not matches:
        logger.warning("No Rx-pps found in output:\n%s", stats_output)
        return None

    total_pps = sum(int(m) for m in matches)
    mpps = total_pps / 1_000_000
    logger.info(
        "Throughput: %s (per-port pps: %s)",
        f"{mpps:.2f} Mpps",
        ", ".join(matches),
    )
    return round(mpps, 4)


def _stop_testpmd(proc: subprocess.Popen) -> None:
    """Gracefully stop testpmd, falling back to kill."""
    if proc.poll() is not None:
        return

    try:
        if proc.stdin and not proc.stdin.closed:
            proc.stdin.write("stop\n")
            proc.stdin.write("quit\n")
            proc.stdin.flush()
        proc.wait(timeout=10)
    except (subprocess.TimeoutExpired, OSError):
        logger.warning("testpmd did not exit gracefully, killing")
        proc.kill()
        proc.wait(timeout=5)
