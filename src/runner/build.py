"""DPDK build orchestration for the runner."""

from __future__ import annotations

import logging
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class BuildResult:
    """Result of a DPDK build attempt."""

    success: bool
    log: str
    duration_seconds: float


def _truncate_log(log: str, max_lines: int = 200) -> str:
    """Keep only the last max_lines of a log string."""
    lines = log.splitlines()
    if len(lines) <= max_lines:
        return log
    return "\n".join(lines[-max_lines:])


def build_dpdk(
    source_path: Path,
    commit: str,
    build_dir: Path,
    timeout: int = 1800,
    build_config: dict | None = None,
) -> BuildResult:
    """Build DPDK from source at a given commit.

    Args:
        source_path: Path to the DPDK source tree.
        commit: Git commit hash to check out.
        build_dir: Directory for meson build artefacts.
        timeout: Maximum seconds before the build is killed.
        build_config: Optional [build] config dict with keys:
            jobs, cross_file, extra_meson_args.

    Returns:
        A BuildResult with success status, build log, and duration.
    """
    cfg = build_config or {}
    start = time.monotonic()
    combined_log: list[str] = []

    try:
        logger.info("Checking out commit %s in %s", commit[:12], source_path)
        checkout = subprocess.run(
            ["git", "-C", str(source_path), "checkout", commit],
            capture_output=True,
            text=True,
            timeout=60,
        )
        combined_log.append(checkout.stdout)
        combined_log.append(checkout.stderr)
        if checkout.returncode != 0:
            logger.error("Git checkout failed: %s", checkout.stderr.strip())
            duration = time.monotonic() - start
            log = _truncate_log("\n".join(combined_log))
            return BuildResult(success=False, log=log, duration_seconds=duration)

        meson_configured = (build_dir / "meson-private" / "build.dat").exists()
        if meson_configured:
            meson_cmd = [
                "meson",
                "setup",
                "--reconfigure",
                str(build_dir),
                str(source_path),
            ]
        else:
            if build_dir.exists():
                shutil.rmtree(build_dir)
                logger.info("Removed stale build dir %s", build_dir)
            meson_cmd = ["meson", "setup", str(build_dir), str(source_path)]

        cross_file = cfg.get("cross_file", "")
        if cross_file:
            meson_cmd.extend(["--cross-file", cross_file])

        extra_args = cfg.get("extra_meson_args", "")
        if extra_args:
            meson_cmd.extend(extra_args.split())

        logger.info("Running meson: %s", " ".join(meson_cmd))
        meson = subprocess.run(
            meson_cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        combined_log.append(meson.stdout)
        combined_log.append(meson.stderr)
        if meson.returncode != 0:
            logger.error("Meson setup failed (exit %d)", meson.returncode)
            duration = time.monotonic() - start
            log = _truncate_log("\n".join(combined_log))
            return BuildResult(success=False, log=log, duration_seconds=duration)

        remaining = max(10, timeout - int(time.monotonic() - start))
        ninja_cmd = ["ninja", "-C", str(build_dir)]
        jobs = int(cfg.get("jobs", 0))
        if jobs > 0:
            ninja_cmd.extend(["-j", str(jobs)])

        logger.info("Running ninja: %s", " ".join(ninja_cmd))
        ninja = subprocess.run(
            ninja_cmd,
            capture_output=True,
            text=True,
            timeout=remaining,
        )
        combined_log.append(ninja.stdout)
        combined_log.append(ninja.stderr)

        duration = time.monotonic() - start
        if ninja.returncode != 0:
            logger.error("Ninja build failed (exit %d)", ninja.returncode)
            log = _truncate_log("\n".join(combined_log))
            return BuildResult(success=False, log=log, duration_seconds=duration)

        logger.info("Build succeeded in %.1fs", duration)
        return BuildResult(
            success=True,
            log="\n".join(combined_log),
            duration_seconds=duration,
        )

    except subprocess.TimeoutExpired:
        duration = time.monotonic() - start
        combined_log.append(f"\n[BUILD TIMEOUT after {duration:.0f}s]")
        log = _truncate_log("\n".join(combined_log))
        logger.error("Build timed out after %.0fs", duration)
        return BuildResult(success=False, log=log, duration_seconds=duration)
