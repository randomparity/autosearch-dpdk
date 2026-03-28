"""Tests for strategy module: context formatting and change validation."""

from __future__ import annotations

import subprocess
from unittest.mock import patch

from autoforge.agent.strategy import (
    check_scope_compliance,
    format_context,
    has_submodule_change,
)


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

    def test_shows_comparison_mode_when_non_default(self) -> None:
        campaign = {
            **SAMPLE_CAMPAIGN,
            "metric": {
                **SAMPLE_CAMPAIGN["metric"],
                "comparison": "rolling_average",
                "comparison_window": 10,
            },
        }
        result = format_context([], campaign)
        assert "Comparison: rolling_average (window=10)" in result

    def test_hides_comparison_mode_when_peak(self) -> None:
        result = format_context([], SAMPLE_CAMPAIGN)
        assert "Comparison:" not in result


def _fake_run(stdout: str, returncode: int = 0):
    """Build a fake subprocess.CompletedProcess."""
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")


class TestHasSubmoduleChange:
    def test_unstaged_only(self, tmp_path) -> None:
        """Unstaged diff has output, cached is empty → True."""
        with patch("autoforge.agent.strategy.subprocess.run") as mock_run:
            mock_run.side_effect = [
                _fake_run("Submodule projects/dpdk/repo abc..def"),
                # --cached call not needed since first returned True
            ]
            assert has_submodule_change(tmp_path / "repo") is True
            assert mock_run.call_count == 1

    def test_staged_only(self, tmp_path) -> None:
        """Unstaged is empty, cached has output → True."""
        with patch("autoforge.agent.strategy.subprocess.run") as mock_run:
            mock_run.side_effect = [
                _fake_run(""),  # unstaged: nothing
                _fake_run("Submodule projects/dpdk/repo abc..def"),  # cached: changed
            ]
            assert has_submodule_change(tmp_path / "repo") is True
            assert mock_run.call_count == 2

    def test_both_changed(self, tmp_path) -> None:
        """Both unstaged and cached have output → True (short-circuits on first)."""
        with patch("autoforge.agent.strategy.subprocess.run") as mock_run:
            mock_run.side_effect = [
                _fake_run("Submodule diff"),
            ]
            assert has_submodule_change(tmp_path / "repo") is True
            assert mock_run.call_count == 1

    def test_neither_changed(self, tmp_path) -> None:
        """Neither has output → False."""
        with patch("autoforge.agent.strategy.subprocess.run") as mock_run:
            mock_run.side_effect = [
                _fake_run(""),
                _fake_run(""),
            ]
            assert has_submodule_change(tmp_path / "repo") is False

    def test_git_failure_raises(self, tmp_path) -> None:
        """Non-zero return code raises CalledProcessError."""
        import pytest

        with patch("autoforge.agent.strategy.subprocess.run") as mock_run:
            mock_run.return_value = _fake_run("", returncode=128)
            with pytest.raises(subprocess.CalledProcessError):
                has_submodule_change(tmp_path / "repo")


class TestCheckScopeCompliance:
    def test_empty_scope_returns_empty(self, tmp_path) -> None:
        assert check_scope_compliance(tmp_path, []) == []

    def test_all_in_scope(self, tmp_path) -> None:
        with patch("autoforge.agent.strategy.subprocess.run") as mock_run:
            mock_run.return_value = _fake_run(
                "drivers/net/memif/rte_eth_memif.c\ndrivers/net/memif/memif.h\n"
            )
            result = check_scope_compliance(tmp_path, ["drivers/net/memif/"])
            assert result == []

    def test_some_out_of_scope(self, tmp_path) -> None:
        with patch("autoforge.agent.strategy.subprocess.run") as mock_run:
            mock_run.return_value = _fake_run(
                "drivers/net/memif/rte_eth_memif.c\nlib/ethdev/rte_ethdev.c\n"
            )
            result = check_scope_compliance(tmp_path, ["drivers/net/memif/"])
            assert result == ["lib/ethdev/rte_ethdev.c"]

    def test_no_changed_files(self, tmp_path) -> None:
        with patch("autoforge.agent.strategy.subprocess.run") as mock_run:
            mock_run.return_value = _fake_run("")
            result = check_scope_compliance(tmp_path, ["drivers/net/memif/"])
            assert result == []

    def test_scope_without_trailing_slash(self, tmp_path) -> None:
        with patch("autoforge.agent.strategy.subprocess.run") as mock_run:
            mock_run.return_value = _fake_run("drivers/net/memif/rte_eth_memif.c\n")
            result = check_scope_compliance(tmp_path, ["drivers/net/memif"])
            assert result == []

    def test_git_failure_returns_empty(self, tmp_path) -> None:
        with patch("autoforge.agent.strategy.subprocess.run") as mock_run:
            mock_run.return_value = _fake_run("", returncode=1)
            result = check_scope_compliance(tmp_path, ["drivers/net/memif/"])
            assert result == []
