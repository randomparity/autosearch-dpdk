"""Tests for the shared judge-dispatch helper."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from autoforge.agent.judge import apply_judge_verdict
from autoforge.plugins.protocols import JudgeVerdict

SAMPLE_CAMPAIGN_NO_JUDGE = {
    "campaign": {"name": "test", "max_iterations": 50},
    "metric": {"name": "throughput_mpps", "path": "throughput_mpps", "direction": "maximize"},
    "agent": {"poll_interval": 5, "timeout_minutes": 1},
    "project": {
        "name": "dpdk",
        "build": "local",
        "deploy": "local",
        "test": "testpmd-memif",
        "submodule_path": "projects/dpdk/repo",
        "optimization_branch": "autoforge/optimize",
    },
}

SAMPLE_CAMPAIGN_WITH_JUDGE = {
    **SAMPLE_CAMPAIGN_NO_JUDGE,
    "project": {
        **SAMPLE_CAMPAIGN_NO_JUDGE["project"],
        "judge": "custom",
    },
}


def _make_ctx(tmp_path: Path):
    from autoforge.agent.git_ops import ResultContext

    return ResultContext(
        seq=1,
        commit="abc123",
        description="test change",
        source_path=tmp_path / "dpdk",
        results_path=tmp_path / "results.tsv",
        failures_path=tmp_path / "failures.tsv",
        optimization_branch="autoforge/optimize",
    )


class TestApplyJudgeVerdictNoPlugin:
    def test_calls_record_result_or_revert(self, tmp_path: Path) -> None:
        ctx = _make_ctx(tmp_path)

        with patch("autoforge.agent.judge.record_result_or_revert") as mock_default:
            apply_judge_verdict(90.0, 86.0, "maximize", SAMPLE_CAMPAIGN_NO_JUDGE, None, ctx)

        mock_default.assert_called_once_with(90.0, 86.0, "maximize", ctx, dry_run=False)

    def test_dry_run_forwarded(self, tmp_path: Path) -> None:
        ctx = _make_ctx(tmp_path)

        with patch("autoforge.agent.judge.record_result_or_revert") as mock_default:
            apply_judge_verdict(
                None, None, "maximize", SAMPLE_CAMPAIGN_NO_JUDGE, None, ctx, dry_run=True
            )

        _, kwargs = mock_default.call_args
        assert kwargs["dry_run"] is True


class TestApplyJudgeVerdictWithPlugin:
    def _make_mock_judge(self, keep: bool, reason: str):
        mock_judge = MagicMock()
        mock_judge.judge.return_value = JudgeVerdict(keep=keep, reason=reason)
        return mock_judge

    def test_loads_and_calls_plugin(self, tmp_path: Path) -> None:
        ctx = _make_ctx(tmp_path)
        mock_judge = self._make_mock_judge(keep=True, reason="above threshold")

        with (
            patch("autoforge.agent.judge.load_judge", return_value=mock_judge) as mock_load,
            patch("autoforge.agent.judge.record_verdict") as mock_record,
        ):
            apply_judge_verdict(90.0, 86.0, "maximize", SAMPLE_CAMPAIGN_WITH_JUDGE, None, ctx)

        mock_load.assert_called_once_with(
            "dpdk", "custom", project_config=SAMPLE_CAMPAIGN_WITH_JUDGE["project"], runner_config={}
        )
        mock_judge.judge.assert_called_once()
        mock_record.assert_called_once_with(True, 90.0, 86.0, ctx, dry_run=False)

    def test_configure_called_via_runner_config(self, tmp_path: Path) -> None:
        """load_judge receives runner_config={} so configure() is called."""
        ctx = _make_ctx(tmp_path)
        mock_judge = self._make_mock_judge(keep=False, reason="no improvement")

        with (
            patch("autoforge.agent.judge.load_judge", return_value=mock_judge) as mock_load,
            patch("autoforge.agent.judge.record_verdict"),
        ):
            apply_judge_verdict(85.0, 86.0, "maximize", SAMPLE_CAMPAIGN_WITH_JUDGE, None, ctx)

        _, kwargs = mock_load.call_args
        assert "runner_config" in kwargs
        assert kwargs["runner_config"] == {}

    def test_verdict_reason_printed(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        ctx = _make_ctx(tmp_path)
        mock_judge = self._make_mock_judge(keep=True, reason="custom logic says keep")

        with (
            patch("autoforge.agent.judge.load_judge", return_value=mock_judge),
            patch("autoforge.agent.judge.record_verdict"),
        ):
            apply_judge_verdict(90.0, 86.0, "maximize", SAMPLE_CAMPAIGN_WITH_JUDGE, None, ctx)

        out = capsys.readouterr().out
        assert "custom" in out
        assert "keep" in out
        assert "custom logic says keep" in out

    def test_revert_verdict_printed(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        ctx = _make_ctx(tmp_path)
        mock_judge = self._make_mock_judge(keep=False, reason="below threshold")

        with (
            patch("autoforge.agent.judge.load_judge", return_value=mock_judge),
            patch("autoforge.agent.judge.record_verdict"),
        ):
            apply_judge_verdict(80.0, 86.0, "maximize", SAMPLE_CAMPAIGN_WITH_JUDGE, None, ctx)

        out = capsys.readouterr().out
        assert "revert" in out
        assert "below threshold" in out

    def test_does_not_call_record_result_or_revert(self, tmp_path: Path) -> None:
        ctx = _make_ctx(tmp_path)
        mock_judge = self._make_mock_judge(keep=True, reason="ok")

        with (
            patch("autoforge.agent.judge.load_judge", return_value=mock_judge),
            patch("autoforge.agent.judge.record_verdict"),
            patch("autoforge.agent.judge.record_result_or_revert") as mock_default,
        ):
            apply_judge_verdict(90.0, 86.0, "maximize", SAMPLE_CAMPAIGN_WITH_JUDGE, None, ctx)

        mock_default.assert_not_called()
