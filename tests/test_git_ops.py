"""Tests for git operations."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from autoforge.agent.git_ops import GIT_TIMEOUT, force_push_source, full_revert, push_submodule


class TestPushSubmodule:
    def test_runs_git_push_without_force(self) -> None:
        source_path = Path("/opt/dpdk")
        with patch("autoforge.agent.git_ops.subprocess.run") as mock_run:
            push_submodule(source_path, "autoforge/optimize")

        mock_run.assert_called_once_with(
            ["git", "-C", "/opt/dpdk", "push", "origin", "autoforge/optimize"],
            check=True,
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT,
        )

    def test_propagates_error(self) -> None:
        import subprocess

        source_path = Path("/opt/dpdk")
        with (
            patch(
                "autoforge.agent.git_ops.subprocess.run",
                side_effect=subprocess.CalledProcessError(1, "git push"),
            ),
            pytest.raises(subprocess.CalledProcessError),
        ):
            push_submodule(source_path, "autoforge/optimize")


class TestForcePushSubmodule:
    def test_runs_git_push_force(self) -> None:
        source_path = Path("/opt/dpdk")
        with patch("autoforge.agent.git_ops.subprocess.run") as mock_run:
            force_push_source(source_path, "autoforge/optimize")

        mock_run.assert_called_once_with(
            ["git", "-C", "/opt/dpdk", "push", "--force", "origin", "autoforge/optimize"],
            check=True,
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT,
        )


class TestFullRevert:
    def test_sequence(self) -> None:
        source_path = Path("/opt/dpdk")
        with (
            patch("autoforge.agent.git_ops.git_submodule_head", return_value="oldcommit123"),
            patch("autoforge.agent.git_ops.revert_last_change") as mock_revert,
            patch("autoforge.agent.git_ops.force_push_source") as mock_push,
            patch("autoforge.agent.git_ops.git_add_commit_push") as mock_commit,
        ):
            result = full_revert(source_path, "autoforge/optimize", dry_run=False)

        assert result == "oldcommit123"
        mock_revert.assert_called_once_with(source_path)
        mock_push.assert_called_once_with(source_path, "autoforge/optimize")
        mock_commit.assert_called_once()

    def test_dry_run_skips_push(self) -> None:
        source_path = Path("/opt/dpdk")
        with (
            patch("autoforge.agent.git_ops.git_submodule_head", return_value="oldcommit123"),
            patch("autoforge.agent.git_ops.revert_last_change"),
            patch("autoforge.agent.git_ops.force_push_source") as mock_push,
            patch("autoforge.agent.git_ops.git_add_commit_push") as mock_commit,
        ):
            full_revert(source_path, "autoforge/optimize", dry_run=True)

        mock_push.assert_not_called()
        mock_commit.assert_called_once_with(
            [str(source_path)],
            "revert: manual revert of DPDK submodule",
            dry_run=True,
        )


class TestRecordResultOrRevertWithBranch:
    def test_revert_force_pushes_when_branch_set(self, tmp_path: Path) -> None:
        from autoforge.agent.git_ops import ResultContext, record_result_or_revert

        res = tmp_path / "results.tsv"
        res.write_text("sequence\ttimestamp\tsource_commit\tmetric_value\tstatus\tdescription\n")
        fail = tmp_path / "failures.tsv"
        dpdk = tmp_path / "dpdk"
        dpdk.mkdir()

        ctx = ResultContext(
            seq=1,
            commit="abc123",
            description="test",
            source_path=dpdk,
            results_path=res,
            failures_path=fail,
            optimization_branch="autoforge/optimize",
        )

        with (
            patch("autoforge.agent.git_ops.compare_metric", return_value=False),
            patch("autoforge.agent.git_ops.capture_diff_summary", return_value="1 file changed"),
            patch("autoforge.agent.git_ops.revert_last_change"),
            patch("autoforge.agent.git_ops.force_push_source") as mock_push,
            patch("autoforge.agent.git_ops.git_add_commit_push"),
        ):
            result = record_result_or_revert(
                metric=80.0,
                best_val=86.0,
                direction="maximize",
                ctx=ctx,
                dry_run=False,
            )

        assert result is False
        mock_push.assert_called_once_with(dpdk, "autoforge/optimize")

    def test_revert_skips_push_when_no_branch(self, tmp_path: Path) -> None:
        from autoforge.agent.git_ops import ResultContext, record_result_or_revert

        res = tmp_path / "results.tsv"
        res.write_text("sequence\ttimestamp\tsource_commit\tmetric_value\tstatus\tdescription\n")
        fail = tmp_path / "failures.tsv"
        dpdk = tmp_path / "dpdk"
        dpdk.mkdir()

        ctx = ResultContext(
            seq=1,
            commit="abc123",
            description="test",
            source_path=dpdk,
            results_path=res,
            failures_path=fail,
        )

        with (
            patch("autoforge.agent.git_ops.compare_metric", return_value=False),
            patch("autoforge.agent.git_ops.capture_diff_summary", return_value="1 file changed"),
            patch("autoforge.agent.git_ops.revert_last_change"),
            patch("autoforge.agent.git_ops.force_push_source") as mock_push,
            patch("autoforge.agent.git_ops.git_add_commit_push"),
        ):
            record_result_or_revert(
                metric=80.0,
                best_val=86.0,
                direction="maximize",
                ctx=ctx,
                dry_run=False,
            )

        mock_push.assert_not_called()
