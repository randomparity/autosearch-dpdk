"""Tests for git operations."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from src.agent.git_ops import GIT_TIMEOUT, force_push_submodule, full_revert


class TestForcePushSubmodule:
    def test_runs_git_push_force(self) -> None:
        dpdk_path = Path("/opt/dpdk")
        with patch("src.agent.git_ops.subprocess.run") as mock_run:
            force_push_submodule(dpdk_path, "autosearch/optimize")

        mock_run.assert_called_once_with(
            ["git", "-C", "/opt/dpdk", "push", "--force", "origin", "autosearch/optimize"],
            check=True,
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT,
        )


class TestFullRevert:
    def test_sequence(self) -> None:
        dpdk_path = Path("/opt/dpdk")
        with (
            patch("src.agent.git_ops.git_submodule_head", return_value="oldcommit123"),
            patch("src.agent.git_ops.revert_last_change") as mock_revert,
            patch("src.agent.git_ops.force_push_submodule") as mock_push,
            patch("src.agent.git_ops.git_add_commit_push") as mock_commit,
        ):
            result = full_revert(dpdk_path, "autosearch/optimize", dry_run=False)

        assert result == "oldcommit123"
        mock_revert.assert_called_once_with(dpdk_path)
        mock_push.assert_called_once_with(dpdk_path, "autosearch/optimize")
        mock_commit.assert_called_once()

    def test_dry_run_skips_push(self) -> None:
        dpdk_path = Path("/opt/dpdk")
        with (
            patch("src.agent.git_ops.git_submodule_head", return_value="oldcommit123"),
            patch("src.agent.git_ops.revert_last_change"),
            patch("src.agent.git_ops.force_push_submodule") as mock_push,
            patch("src.agent.git_ops.git_add_commit_push") as mock_commit,
        ):
            full_revert(dpdk_path, "autosearch/optimize", dry_run=True)

        mock_push.assert_not_called()
        mock_commit.assert_called_once_with(
            [str(dpdk_path)],
            "revert: manual revert of DPDK submodule",
            dry_run=True,
        )


class TestRecordResultOrRevertWithBranch:
    def test_revert_force_pushes_when_branch_set(self, tmp_path: Path) -> None:
        from src.agent.git_ops import record_result_or_revert

        res = tmp_path / "results.tsv"
        res.write_text("sequence\ttimestamp\tdpdk_commit\tmetric_value\tstatus\tdescription\n")
        fail = tmp_path / "failures.tsv"
        dpdk = tmp_path / "dpdk"
        dpdk.mkdir()

        with (
            patch("src.agent.git_ops.compare_metric", return_value=False),
            patch("src.agent.git_ops.get_diff_summary", return_value="1 file changed"),
            patch("src.agent.git_ops.revert_last_change"),
            patch("src.agent.git_ops.force_push_submodule") as mock_push,
            patch("src.agent.git_ops.git_add_commit_push"),
        ):
            result = record_result_or_revert(
                metric=80.0,
                best_val=86.0,
                direction="maximize",
                seq=1,
                commit="abc123",
                description="test",
                dpdk_path=dpdk,
                dry_run=False,
                results_path=res,
                failures_path=fail,
                optimization_branch="autosearch/optimize",
            )

        assert result is False
        mock_push.assert_called_once_with(dpdk, "autosearch/optimize")

    def test_revert_skips_push_when_no_branch(self, tmp_path: Path) -> None:
        from src.agent.git_ops import record_result_or_revert

        res = tmp_path / "results.tsv"
        res.write_text("sequence\ttimestamp\tdpdk_commit\tmetric_value\tstatus\tdescription\n")
        fail = tmp_path / "failures.tsv"
        dpdk = tmp_path / "dpdk"
        dpdk.mkdir()

        with (
            patch("src.agent.git_ops.compare_metric", return_value=False),
            patch("src.agent.git_ops.get_diff_summary", return_value="1 file changed"),
            patch("src.agent.git_ops.revert_last_change"),
            patch("src.agent.git_ops.force_push_submodule") as mock_push,
            patch("src.agent.git_ops.git_add_commit_push"),
        ):
            record_result_or_revert(
                metric=80.0,
                best_val=86.0,
                direction="maximize",
                seq=1,
                commit="abc123",
                description="test",
                dpdk_path=dpdk,
                dry_run=False,
                results_path=res,
                failures_path=fail,
            )

        mock_push.assert_not_called()
