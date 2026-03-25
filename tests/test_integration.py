"""Integration tests: agent creates request, runner updates, agent evaluates."""

from __future__ import annotations

from pathlib import Path

from autoforge.agent.history import append_result, best_result, load_history
from autoforge.agent.metric import compare_metric
from autoforge.agent.protocol import create_request
from autoforge.protocol import (
    STATUS_COMPLETED,
    STATUS_FAILED,
    TestRequest,
    extract_metric,
)

SAMPLE_CAMPAIGN = {
    "metric": {
        "name": "throughput_mpps",
        "path": "results.throughput_mpps",
        "direction": "maximize",
    },
    "project": {
        "build": "local-server",
        "deploy": "local",
        "test": "testpmd-memif",
    },
}


def simulate_runner_completion(
    request_path: Path,
    metric_value: float | None = None,
    error: str | None = None,
) -> TestRequest:
    """Simulate a runner completing (or failing) a request."""
    req = TestRequest.read(request_path)
    if error:
        req.status = STATUS_FAILED
        req.error = error
        req.completed_at = "2025-01-15T11:00:00"
    else:
        req.status = STATUS_COMPLETED
        req.completed_at = "2025-01-15T11:00:00"
        req.metric_value = metric_value
        req.results_json = {"results": {"throughput_mpps": metric_value}}
        req.results_summary = f"Throughput: {metric_value} Mpps"
    req.write(request_path)
    return req


class TestFullIteration:
    def test_successful_iteration(self, tmp_path) -> None:
        requests_dir = tmp_path / "requests"
        requests_dir.mkdir()
        results_path = tmp_path / "results.tsv"
        results_path.write_text(
            "sequence\ttimestamp\tsource_commit\tmetric_value\tstatus\tdescription\n"
        )

        # Agent creates request
        path = create_request(
            seq=1,
            commit="abc123",
            campaign=SAMPLE_CAMPAIGN,
            description="Increase burst size",
            requests_dir=requests_dir,
        )
        req = TestRequest.read(path)
        assert req.status == "pending"

        # Runner completes it
        completed = simulate_runner_completion(path, metric_value=14.7)
        assert completed.status == STATUS_COMPLETED
        assert completed.metric_value == 14.7

        # Agent evaluates
        result = TestRequest.read(path)
        metric = extract_metric(
            result.results_json,
            SAMPLE_CAMPAIGN["metric"]["path"],
        )
        assert metric == 14.7

        # Record in history
        append_result(
            seq=1,
            commit="abc123",
            metric=metric,
            status="completed",
            description="Increase burst size",
            path=results_path,
        )
        history = load_history(path=results_path)
        assert len(history) == 1

    def test_failed_iteration(self, tmp_path) -> None:
        requests_dir = tmp_path / "requests"
        requests_dir.mkdir()
        results_path = tmp_path / "results.tsv"
        results_path.write_text(
            "sequence\ttimestamp\tsource_commit\tmetric_value\tstatus\tdescription\n"
        )

        path = create_request(
            seq=1,
            commit="bad123",
            campaign=SAMPLE_CAMPAIGN,
            description="Bad change",
            requests_dir=requests_dir,
        )
        simulate_runner_completion(path, error="Build failed: missing header")

        result = TestRequest.read(path)
        assert result.status == STATUS_FAILED
        assert "Build failed" in result.error

        append_result(1, "bad123", None, "failed", "Bad change", path=results_path)
        assert best_result(path=results_path) is None

    def test_metric_improvement_tracking(self, tmp_path) -> None:
        results_path = tmp_path / "results.tsv"
        results_path.write_text(
            "sequence\ttimestamp\tsource_commit\tmetric_value\tstatus\tdescription\n"
        )

        append_result(1, "aaa", 10.0, "completed", "baseline", path=results_path)
        append_result(2, "bbb", 12.0, "completed", "improved", path=results_path)
        append_result(3, "ccc", 11.0, "completed", "regression", path=results_path)

        best = best_result(path=results_path, direction="maximize")
        assert best["source_commit"] == "bbb"

        # Sequence 3 was a regression
        assert not compare_metric(11.0, 12.0, "maximize")
        # Sequence 2 was an improvement
        assert compare_metric(12.0, 10.0, "maximize")
