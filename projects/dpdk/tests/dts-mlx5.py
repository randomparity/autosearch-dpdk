"""DTS test suite runner for mlx5 NICs."""

from __future__ import annotations

import json
import logging
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from autoforge.plugins.protocols import DeployResult, TestResult
from autoforge.protocol import extract_metric

logger = logging.getLogger(__name__)


@dataclass
class DtsResult:
    """Result of a DTS test run."""

    success: bool
    results_json: dict | None
    results_summary: str | None
    metric_value: float | None
    error: str | None
    duration_seconds: float


class DtsMlx5Tester:
    """Runs DPDK Test Suite against mlx5 NICs."""

    name = "dts-mlx5"

    def __init__(self) -> None:
        self._runner_config: dict[str, Any] = {}

    def configure(self, project_config: dict[str, Any], runner_config: dict[str, Any]) -> None:
        self._runner_config = runner_config

    def test(self, deploy_result: DeployResult, timeout: int) -> TestResult:
        paths = self._runner_config.get("paths", {})
        dts_path = Path(paths.get("dts_dir", "/opt/dts"))
        test_cfg = self._runner_config.get("test", {})
        suites = test_cfg.get("test_suites", [])
        perf = test_cfg.get("perf", True)
        metric_path = test_cfg.get("metric_path", "throughput_mpps")

        result = run_dts(
            dts_path=dts_path,
            suites=suites,
            perf=perf,
            metric_path=metric_path,
            timeout=timeout,
        )
        return TestResult(
            success=result.success,
            metric_value=result.metric_value,
            results_json=result.results_json,
            results_summary=result.results_summary,
            error=result.error,
            duration_seconds=result.duration_seconds,
        )


def run_dts(
    dts_path: Path,
    suites: list[str],
    perf: bool,
    metric_path: str,
    timeout: int = 3600,
) -> DtsResult:
    """Run the DTS test suite and collect results."""
    start = time.monotonic()

    cmd = ["poetry", "run", "./main.py"]
    for suite in suites:
        cmd.extend(["--test-suite", suite])
    if perf:
        cmd.append("--perf")

    try:
        result = subprocess.run(
            cmd,
            cwd=str(dts_path),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        duration = time.monotonic() - start

        if result.returncode != 0:
            return DtsResult(
                success=False,
                results_json=None,
                results_summary=None,
                metric_value=None,
                error=f"DTS exited with code {result.returncode}:\n{result.stderr[-2000:]}",
                duration_seconds=duration,
            )

        results_json = _read_json_file(dts_path / "output" / "results.json")
        results_summary = _read_text_file(dts_path / "output" / "results_summary.txt")

        metric_value = None
        if results_json is not None:
            try:
                metric_value = extract_metric(results_json, metric_path)
            except (KeyError, IndexError, ValueError):
                logger.warning("Failed to extract metric at %r", metric_path)

        return DtsResult(
            success=True,
            results_json=results_json,
            results_summary=results_summary,
            metric_value=metric_value,
            error=None,
            duration_seconds=duration,
        )

    except subprocess.TimeoutExpired:
        duration = time.monotonic() - start
        logger.error("DTS timed out after %.0fs", duration)
        return DtsResult(
            success=False,
            results_json=None,
            results_summary=None,
            metric_value=None,
            error=f"DTS timed out after {duration:.0f}s",
            duration_seconds=duration,
        )


def _read_json_file(path: Path) -> dict | None:
    """Read and parse a JSON file, returning None on failure."""
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        logger.warning("Could not read results JSON at %s: %s", path, exc)
        return None


def _read_text_file(path: Path) -> str | None:
    """Read a text file, returning None on failure."""
    try:
        return path.read_text()
    except FileNotFoundError:
        logger.warning("Results summary not found at %s", path)
        return None
