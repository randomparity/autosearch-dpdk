"""Tests for strategy module: context formatting and change validation."""

from __future__ import annotations

from autoforge.agent.strategy import format_context


def _row(seq: str, metric: str, status: str, desc: str) -> dict:
    return {
        "sequence": seq,
        "metric_value": metric,
        "status": status,
        "description": desc,
    }


SAMPLE_CAMPAIGN = {
    "metric": {
        "name": "throughput_mpps",
        "direction": "maximize",
    },
    "project": {
        "scope": ["drivers/net/mlx5"],
    },
    "campaign": {
        "name": "mlx5-opt",
        "max_iterations": 50,
    },
    "goal": {
        "description": "Improve MLX5 driver throughput",
    },
}


class TestFormatContext:
    def test_empty_history(self) -> None:
        result = format_context([], SAMPLE_CAMPAIGN)
        assert "Iterations: 0 / 50" in result
        assert "No successful iterations yet." in result

    def test_with_results(self) -> None:
        row1 = _row("1", "10.0", "completed", "base")
        row2 = _row("2", "12.0", "completed", "improved")
        history = [row1, row2]
        result = format_context(history, SAMPLE_CAMPAIGN)
        assert "Iterations: 2 / 50" in result
        assert "Best so far: 12.0" in result
        assert "Recent attempts:" in result

    def test_minimize_direction(self) -> None:
        campaign = {
            **SAMPLE_CAMPAIGN,
            "metric": {"name": "latency", "direction": "minimize"},
        }
        history = [
            _row("1", "10.0", "completed", "base"),
            _row("2", "5.0", "completed", "better"),
        ]
        result = format_context(history, campaign)
        assert "Best so far: 5.0" in result

    def test_includes_goal(self) -> None:
        result = format_context([], SAMPLE_CAMPAIGN)
        assert "Goal: Improve MLX5 driver throughput" in result

    def test_skips_failed_in_best(self) -> None:
        history = [_row("1", "", "failed", "broke")]
        result = format_context(history, SAMPLE_CAMPAIGN)
        assert "No successful iterations yet." in result

    def test_recent_limited_to_five(self) -> None:
        history = [_row(str(i), str(i), "completed", f"iter {i}") for i in range(10)]
        result = format_context(history, SAMPLE_CAMPAIGN)
        assert "#5" not in result or "iter 5" in result
        assert "iter 9" in result

    def test_includes_hints_tip_when_arch_set(self) -> None:
        campaign = {**SAMPLE_CAMPAIGN, "platform": {"arch": "ppc64le"}}
        result = format_context([], campaign)
        assert "autoforge hints" in result
        assert "ppc64le" in result

    def test_no_hints_tip_when_arch_absent(self) -> None:
        result = format_context([], SAMPLE_CAMPAIGN)
        assert "autoforge hints" not in result

    def test_includes_workload_hints_with_profile(self) -> None:
        campaign = {**SAMPLE_CAMPAIGN, "platform": {"arch": "ppc64le"}}
        profile = {
            "derived_metrics": {"backend_bound": 0.5},
            "top_functions": [],
        }
        result = format_context([], campaign, profile_summary=profile)
        assert "Workload-specific suggestions" in result
        assert "Backend-bound" in result

    def test_no_workload_hints_without_profile(self) -> None:
        campaign = {**SAMPLE_CAMPAIGN, "platform": {"arch": "ppc64le"}}
        result = format_context([], campaign)
        assert "Workload-specific suggestions" not in result
