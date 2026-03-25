"""Perf record profiler — captures perf data during test execution."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from autoforge.plugins.protocols import ProfileResult

logger = logging.getLogger(__name__)


class PerfRecordProfiler:
    """Captures perf record + perf stat profiles."""

    name = "perf-record"

    def __init__(self) -> None:
        self._config: dict[str, Any] = {}

    def configure(self, project_config: dict[str, Any], runner_config: dict[str, Any]) -> None:
        self._config = runner_config.get("profiling", {})

    def profile(self, pid: int, duration: int, config: dict[str, Any]) -> ProfileResult:
        from autoforge.perf.analyze import summarize
        from autoforge.perf.arch import load_arch_profile
        from autoforge.perf.profile import profile_pid

        start = time.monotonic()
        repo_root = Path(__file__).resolve().parent.parent.parent.parent
        output_dir = repo_root / "perf" / "results" / str(int(time.time()))

        merged_config = {**self._config, **config}
        result = profile_pid(
            pid=pid,
            duration=duration,
            output_dir=output_dir,
            frequency=merged_config.get("frequency", 99),
            sudo=merged_config.get("sudo", True),
            cpus=merged_config.get("cpus", "0"),
        )

        elapsed = time.monotonic() - start

        if not result.success:
            logger.warning("Profiling failed: %s", result.error)
            return ProfileResult(
                success=False,
                error=result.error,
                duration_seconds=elapsed,
            )

        profile = load_arch_profile()
        summary = summarize(result.counters, result.folded_stacks, profile)

        return ProfileResult(
            success=True,
            summary=summary or {},
            duration_seconds=elapsed,
        )
