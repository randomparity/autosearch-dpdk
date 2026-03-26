"""Core perf profiling: capture, folded-stack parsing, counter parsing."""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from autoforge.perf.arch import COMMON_EVENTS, load_arch_profile

logger = logging.getLogger(__name__)

PERF_TIMEOUT_MARGIN = 30  # extra seconds beyond duration for perf to finish


@dataclass
class ProfileResult:
    """Result of a perf profiling capture."""

    success: bool
    folded_stacks: dict[str, int] = field(default_factory=dict)
    counters: dict[str, float] = field(default_factory=dict)
    error: str | None = None
    duration_seconds: float = 0.0


def _build_cmd(args: list[str], *, sudo: bool) -> list[str]:
    """Prepend sudo to a command if requested."""
    if sudo:
        return ["sudo", *args]
    return args


def profile_pid(
    pid: int,
    duration: int,
    output_dir: Path,
    *,
    arch: str | None = None,
    frequency: int = 99,
    sudo: bool = False,
    cpus: str | None = None,
) -> ProfileResult:
    """Capture perf record + perf stat against a running process.

    Args:
        pid: Target process ID.
        duration: Capture duration in seconds.
        output_dir: Directory for artifacts (perf.data, folded stacks).
        arch: Architecture key for event selection. Auto-detected if None.
        frequency: Sampling frequency in Hz.
        sudo: Whether to run perf commands with sudo.
        cpus: CPU list for system-wide profiling (e.g. "4-12"). When set,
            uses -a -C instead of -p to capture all threads on those cores.

    Returns:
        ProfileResult with folded stacks and counter data.
    """
    start = time.monotonic()

    if not shutil.which("perf"):
        return ProfileResult(
            success=False,
            error="perf binary not found in PATH",
            duration_seconds=time.monotonic() - start,
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    perf_data = output_dir / "perf.data"
    timeout = duration + PERF_TIMEOUT_MARGIN

    profile = load_arch_profile(arch)
    events = list(profile.get("events", {}).values()) or COMMON_EVENTS

    # Use CPU-based targeting when lcores are specified (captures all threads),
    # otherwise fall back to PID-based targeting.
    if cpus:
        target_args = ["-a", "-C", cpus]
        logger.info("Profiling CPUs %s (system-wide on those cores)", cpus)
    else:
        target_args = ["-p", str(pid)]

    # Launch perf record and perf stat in parallel
    record_cmd = _build_cmd(
        [
            "perf",
            "record",
            "--call-graph",
            "dwarf,16384",
            "-F",
            str(frequency),
            *target_args,
            "-o",
            str(perf_data),
            "--",
            "sleep",
            str(duration),
        ],
        sudo=sudo,
    )
    stat_cmd = _build_cmd(
        [
            "perf",
            "stat",
            "-e",
            ",".join(events),
            *target_args,
            "--",
            "sleep",
            str(duration),
        ],
        sudo=sudo,
    )

    logger.info("Starting perf record (pid=%d, %ds, %dHz)", pid, duration, frequency)
    try:
        record_proc = subprocess.Popen(
            record_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except OSError as exc:
        return ProfileResult(
            success=False,
            error=f"Failed to start perf record: {exc}",
            duration_seconds=time.monotonic() - start,
        )
    try:
        stat_proc = subprocess.Popen(
            stat_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except OSError as exc:
        record_proc.kill()
        record_proc.wait(timeout=10)
        return ProfileResult(
            success=False,
            error=f"Failed to start perf stat: {exc}",
            duration_seconds=time.monotonic() - start,
        )

    try:
        deadline = time.monotonic() + timeout
        _, record_stderr = record_proc.communicate(timeout=timeout)
        remaining = max(5.0, deadline - time.monotonic())
        _, stat_stderr = stat_proc.communicate(timeout=remaining)
    except subprocess.TimeoutExpired:
        record_proc.kill()
        stat_proc.kill()
        record_proc.wait(timeout=10)
        stat_proc.wait(timeout=10)
        return ProfileResult(
            success=False,
            error="perf timed out",
            duration_seconds=time.monotonic() - start,
        )

    record_stderr_text = record_stderr.decode(errors="replace")
    stat_stderr_text = stat_stderr.decode(errors="replace")

    logger.debug("perf record rc=%d stderr: %s", record_proc.returncode, record_stderr_text[:500])
    logger.debug("perf stat rc=%d stderr: %s", stat_proc.returncode, stat_stderr_text[:500])

    if perf_data.exists():
        logger.debug("perf.data size: %d bytes", perf_data.stat().st_size)
    else:
        logger.warning("perf.data not found at %s", perf_data)

    if record_proc.returncode != 0:
        return ProfileResult(
            success=False,
            error=f"perf record failed: {record_stderr_text[:500]}",
            duration_seconds=time.monotonic() - start,
        )

    if stat_proc.returncode != 0:
        # Non-fatal: perf stat counters are supplementary to perf record.
        # The profile result is still usable from the recorded data alone.
        logger.warning(
            "perf stat failed (rc=%d), continuing without counters: %s",
            stat_proc.returncode,
            stat_stderr_text[:300],
        )

    # Post-process: perf script → folded stacks
    script_cmd = _build_cmd(
        ["perf", "script", "-i", str(perf_data)],
        sudo=sudo,
    )
    script_result = subprocess.run(
        script_cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    logger.debug(
        "perf script rc=%d, stdout=%d bytes, stderr=%s",
        script_result.returncode,
        len(script_result.stdout),
        script_result.stderr[:300],
    )
    if script_result.returncode != 0:
        return ProfileResult(
            success=False,
            error=f"perf script failed: {script_result.stderr[:500]}",
            duration_seconds=time.monotonic() - start,
        )

    stacks = fold_stacks(script_result.stdout)
    folded_path = output_dir / "stacks.folded"
    write_folded(stacks, folded_path)

    counters = parse_perf_stat(stat_stderr.decode(errors="replace"))

    logger.info(
        "Profiling complete: %d unique stacks, %d counters",
        len(stacks),
        len(counters),
    )
    return ProfileResult(
        success=True,
        folded_stacks=stacks,
        counters=counters,
        duration_seconds=time.monotonic() - start,
    )


def fold_stacks(perf_script_output: str) -> dict[str, int]:
    """Parse perf script output into folded stacks.

    Args:
        perf_script_output: Raw text from `perf script`.

    Returns:
        Dict mapping semicolon-delimited stack strings to sample counts.
    """
    stacks: dict[str, int] = {}
    current_frames: list[str] = []

    for line in perf_script_output.splitlines():
        stripped = line.strip()

        if not stripped:
            # Blank line ends a record
            if current_frames:
                # Frames are bottom-up from perf script; reverse for caller→callee
                stack_key = ";".join(reversed(current_frames))
                stacks[stack_key] = stacks.get(stack_key, 0) + 1
                current_frames = []
            continue

        if stripped.startswith(("(", "#")):
            continue

        # Frame lines start with hex address
        parts = stripped.split(None, 1)
        if len(parts) >= 2 and _is_hex(parts[0]):
            raw = parts[1].split("(")[0].strip()
            # Strip offset like "+0x20" to get the bare symbol name
            symbol = raw.split("+")[0] if raw else parts[0]
            current_frames.append(symbol)

    # Handle last record if no trailing blank line
    if current_frames:
        stack_key = ";".join(reversed(current_frames))
        stacks[stack_key] = stacks.get(stack_key, 0) + 1

    return stacks


def _is_hex(s: str) -> bool:
    """Check if a string looks like a hex address."""
    try:
        int(s, 16)
    except ValueError:
        return False
    return True


_STAT_LINE_RE = re.compile(r"^\s*([\d,]+)\s+(\S+)")


def parse_perf_stat(raw_output: str) -> dict[str, float]:
    """Parse perf stat text output into a dict of event values.

    Args:
        raw_output: Raw stderr text from `perf stat`.

    Returns:
        Dict mapping event names to numeric values.
    """
    counters: dict[str, float] = {}
    for line in raw_output.splitlines():
        match = _STAT_LINE_RE.match(line)
        if match:
            value_str = match.group(1).replace(",", "")
            event_name = match.group(2)
            try:
                counters[event_name] = float(value_str)
            except ValueError:
                continue
    return counters


def write_folded(stacks: dict[str, int], path: Path) -> None:
    """Write folded stacks in Brendan Gregg format.

    Each line: 'frame1;frame2;frame3 count'
    """
    with open(path, "w") as f:
        for stack, count in sorted(stacks.items(), key=lambda x: -x[1]):
            f.write(f"{stack} {count}\n")
