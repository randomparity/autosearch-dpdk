"""vLLM serving benchmark — runs vllm bench serve inside the container."""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from autoforge.plugins.protocols import DeployResult, TestResult

if TYPE_CHECKING:
    from autoforge.campaign import ProjectConfig

logger = logging.getLogger(__name__)

# Regex fallback patterns — keys match the JSON output from `vllm bench serve`.
METRIC_PATTERNS: dict[str, str] = {
    "output_throughput": r"Output token throughput \(tok/s\):\s+([\d.]+)",
    "total_token_throughput": r"Total [Tt]oken throughput \(tok/s\):\s+([\d.]+)",
    "request_throughput": r"Request throughput \(req/s\):\s+([\d.]+)",
    "mean_ttft_ms": r"Mean TTFT \(ms\):\s+([\d.]+)",
    "median_ttft_ms": r"Median TTFT \(ms\):\s+([\d.]+)",
    "p99_ttft_ms": r"P99 TTFT \(ms\):\s+([\d.]+)",
    "mean_tpot_ms": r"Mean TPOT \(ms\):\s+([\d.]+)",
    "median_tpot_ms": r"Median TPOT \(ms\):\s+([\d.]+)",
    "p99_tpot_ms": r"P99 TPOT \(ms\):\s+([\d.]+)",
    "mean_itl_ms": r"Mean ITL \(ms\):\s+([\d.]+)",
    "p99_itl_ms": r"P99 ITL \(ms\):\s+([\d.]+)",
}

# Result path inside the container (not user-configurable).
_CONTAINER_RESULT_DIR = "/tmp/vllm-bench"
_CONTAINER_RESULT_FILE = "result.json"


class VllmServingBenchTester:
    """Runs vllm bench serve inside the deployed container."""

    name = "bench-serving"

    def configure(self, project_config: ProjectConfig, runner_config: dict[str, Any]) -> None:
        cfg = runner_config.get("bench", {})
        self._num_prompts = int(cfg.get("num_prompts", 100))
        self._dataset = cfg.get("dataset_name", "random")
        self._input_len = int(cfg.get("random_input_len", 512))
        self._output_len = int(cfg.get("random_output_len", 256))
        self._max_concurrency = int(cfg.get("max_concurrency", 64))
        self._request_rate = str(cfg.get("request_rate", "inf"))

    def test(self, deploy_result: DeployResult, timeout: int) -> TestResult:
        model = deploy_result.target_info.get("model", "unknown")
        container = deploy_result.target_info.get("container_name", "vllm-bench")
        runtime = deploy_result.target_info.get("runtime", "docker")

        local_result_dir = Path(tempfile.mkdtemp(prefix="vllm-bench-"))
        start = time.monotonic()
        try:
            cmd = [
                runtime,
                "exec",
                container,
                "vllm",
                "bench",
                "serve",
                "--backend",
                "vllm",
                "--base-url",
                "http://localhost:8000",
                "--model",
                model,
                "--dataset-name",
                self._dataset,
                "--num-prompts",
                str(self._num_prompts),
                "--max-concurrency",
                str(self._max_concurrency),
                "--request-rate",
                self._request_rate,
                "--save-result",
                "--result-dir",
                _CONTAINER_RESULT_DIR,
                "--result-filename",
                _CONTAINER_RESULT_FILE,
                "--percentile-metrics",
                "ttft,tpot,itl",
            ]
            if self._dataset == "random":
                cmd.extend(
                    [
                        "--random-input-len",
                        str(self._input_len),
                        "--random-output-len",
                        str(self._output_len),
                    ]
                )

            logger.info("Running benchmark: %s", " ".join(cmd))
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            elapsed = time.monotonic() - start

            if result.returncode != 0:
                return TestResult(
                    success=False,
                    metric_value=None,
                    results_json=None,
                    results_summary=None,
                    error=result.stderr[-1000:],
                    duration_seconds=elapsed,
                )

            local_result_file = _copy_result_from_container(
                runtime,
                container,
                local_result_dir,
            )
            metrics = _parse_results(local_result_file, result.stdout)
            output_tput = metrics.get("output_throughput")
            return TestResult(
                success=True,
                metric_value=output_tput,
                results_json=metrics,
                results_summary=_format_summary(metrics),
                error=None,
                duration_seconds=elapsed,
            )
        except subprocess.TimeoutExpired:
            return TestResult(
                success=False,
                metric_value=None,
                results_json=None,
                results_summary=None,
                error="benchmark timed out",
                duration_seconds=time.monotonic() - start,
            )
        finally:
            subprocess.run(
                [runtime, "rm", "-f", container],
                capture_output=True,
                timeout=30,
            )
            shutil.rmtree(local_result_dir, ignore_errors=True)


def _copy_result_from_container(
    runtime: str,
    container: str,
    local_dir: Path,
) -> Path | None:
    """Copy the JSON result file from the container to a local path."""
    local_file = local_dir / _CONTAINER_RESULT_FILE
    src = f"{container}:{_CONTAINER_RESULT_DIR}/{_CONTAINER_RESULT_FILE}"
    cp = subprocess.run(
        [runtime, "cp", src, str(local_file)],
        capture_output=True,
        timeout=30,
    )
    if cp.returncode != 0:
        logger.warning("Failed to copy result file from container: %s", cp.stderr)
        return None
    return local_file


def _parse_results(result_file: Path | None, stdout: str) -> dict[str, Any]:
    """Parse benchmark results from JSON file or stdout regex fallback."""
    if result_file and result_file.exists():
        try:
            with open(result_file) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass

    metrics: dict[str, Any] = {}
    for key, pattern in METRIC_PATTERNS.items():
        match = re.search(pattern, stdout)
        if match:
            metrics[key] = float(match.group(1))
    return metrics


def _format_summary(metrics: dict[str, Any]) -> str:
    tput = metrics.get("output_throughput", 0)
    ttft = metrics.get("median_ttft_ms", 0)
    tpot = metrics.get("median_tpot_ms", 0)
    return f"{tput:.1f} tok/s output | TTFT {ttft:.1f}ms | TPOT {tpot:.2f}ms"
