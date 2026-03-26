"""Tests for CLI subcommands."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from autoforge.agent.cli import (
    _format_build_log,
    cmd_build_log,
    cmd_hints,
    cmd_revert,
    cmd_sprint_active,
    cmd_sprint_init,
    cmd_sprint_list,
)
from autoforge.agent.git_ops import DirtyWorkingTreeError, check_git_clean

SAMPLE_CAMPAIGN = {
    "campaign": {"name": "test", "max_iterations": 50},
    "metric": {"name": "throughput_mpps", "path": "throughput_mpps", "direction": "maximize"},
    "agent": {"poll_interval": 5, "timeout_minutes": 1},
    "project": {
        "build": "local",
        "deploy": "local",
        "test": "testpmd-memif",
        "submodule_path": "dpdk",
        "optimization_branch": "autoforge/optimize",
    },
}

SAMPLE_POINTER = {"project": "dpdk", "sprint": "2026-01-01-test"}


class TestCheckGitClean:
    def test_clean_tree_passes(self) -> None:
        with patch("autoforge.agent.git_ops.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = ""
            check_git_clean()

    def test_dirty_tree_raises(self) -> None:
        with patch("autoforge.agent.git_ops.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = " M some/file.py\n"
            with pytest.raises(DirtyWorkingTreeError, match="some/file.py"):
                check_git_clean()

    def test_untracked_files_ignored(self) -> None:
        with patch("autoforge.agent.git_ops.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = "?? .claude/\n?? scorecard.png\n"
            check_git_clean()

    def test_git_failure_raises(self) -> None:
        with patch("autoforge.agent.git_ops.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 128
            mock_run.return_value.stderr = "fatal: not a git repository"
            with pytest.raises(DirtyWorkingTreeError, match="git status failed"):
                check_git_clean()


class TestCmdSprintInit:
    def test_success(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        with patch("autoforge.agent.cli.init_sprint") as mock_init:
            mock_init.return_value = tmp_path / "sprints" / "2026-03-25-test"
            cmd_sprint_init("2026-03-25-test")

        captured = capsys.readouterr()
        assert "Sprint initialized" in captured.out
        mock_init.assert_called_once_with("2026-03-25-test", template=None, from_sprint=None)

    def test_duplicate_exits(self) -> None:
        with (
            patch("autoforge.agent.cli.init_sprint", side_effect=FileExistsError("already exists")),
            pytest.raises(SystemExit, match="1"),
        ):
            cmd_sprint_init("2026-03-25-test")

    def test_invalid_name_exits(self) -> None:
        with (
            patch("autoforge.agent.cli.init_sprint", side_effect=ValueError("Must match")),
            pytest.raises(SystemExit, match="1"),
        ):
            cmd_sprint_init("BAD")


class TestCmdSprintList:
    def test_no_sprints(self, capsys: pytest.CaptureFixture) -> None:
        with patch("autoforge.agent.cli.list_sprints", return_value=[]):
            cmd_sprint_list(SAMPLE_CAMPAIGN)

        captured = capsys.readouterr()
        assert "No sprints found" in captured.out

    def test_with_sprints(self, capsys: pytest.CaptureFixture) -> None:
        sprints = [
            {"name": "2026-01-01-test", "iterations": 5, "max_metric": 86.25},
            {"name": "2026-02-01-next", "iterations": 0, "max_metric": None},
        ]
        with (
            patch("autoforge.agent.cli.list_sprints", return_value=sprints),
            patch("autoforge.agent.cli.active_sprint_name", return_value="2026-01-01-test"),
        ):
            cmd_sprint_list(SAMPLE_CAMPAIGN)

        captured = capsys.readouterr()
        assert "2026-01-01-test" in captured.out
        assert "86.25" in captured.out
        assert "(active)" in captured.out
        assert "no data" in captured.out


class TestCmdSprintActive:
    def test_active_sprint(self, capsys: pytest.CaptureFixture) -> None:
        with patch("autoforge.agent.cli.active_sprint_name", return_value="2026-01-01-test"):
            cmd_sprint_active(SAMPLE_CAMPAIGN)
        captured = capsys.readouterr()
        assert "2026-01-01-test" in captured.out

    def test_no_active_sprint(self) -> None:
        campaign: dict = {"campaign": {"name": "test"}}
        with (
            patch(
                "autoforge.agent.cli.active_sprint_name",
                side_effect=KeyError("No active sprint"),
            ),
            pytest.raises(SystemExit, match="1"),
        ):
            cmd_sprint_active(campaign)


class TestCmdRevert:
    def test_revert_calls_full_revert(self, capsys: pytest.CaptureFixture) -> None:
        with (
            patch("autoforge.agent.cli.check_git_clean"),
            patch("autoforge.agent.cli.full_revert", return_value="abc123def456") as mock_revert,
            patch("autoforge.agent.cli.git_submodule_head", return_value="def456abc123"),
        ):
            cmd_revert(SAMPLE_CAMPAIGN, dry_run=False)

        mock_revert.assert_called_once_with(
            Path("dpdk"),
            "autoforge/optimize",
            False,
        )
        captured = capsys.readouterr()
        assert "abc123def456" in captured.out
        assert "def456abc123" in captured.out
        assert "Force-pushed" in captured.out

    def test_revert_dry_run(self, capsys: pytest.CaptureFixture) -> None:
        with (
            patch("autoforge.agent.cli.full_revert", return_value="abc123def456"),
            patch("autoforge.agent.cli.git_submodule_head", return_value="def456abc123"),
        ):
            cmd_revert(SAMPLE_CAMPAIGN, dry_run=True)

        captured = capsys.readouterr()
        assert "dry-run" in captured.out


class TestFormatBuildLog:
    def test_error_lines_highlighted(self) -> None:
        log = "compiling foo.c\nerror: undefined symbol\nok"
        formatted = _format_build_log(log)
        assert ">>> error: undefined symbol" in formatted

    def test_fatal_highlighted(self) -> None:
        log = "fatal: something broke"
        assert ">>> fatal:" in _format_build_log(log)

    def test_normal_lines_indented(self) -> None:
        log = "compiling bar.c"
        assert _format_build_log(log).startswith("    ")


class TestCmdBuildLog:
    def test_build_log_not_found(self) -> None:
        campaign = {**SAMPLE_CAMPAIGN}
        with (
            patch("autoforge.agent.cli.find_request_by_seq", return_value=None),
            pytest.raises(SystemExit, match="1"),
        ):
            cmd_build_log(campaign, seq=99)

    def test_build_log_empty(self, capsys: pytest.CaptureFixture) -> None:
        mock_req = type("Req", (), {"build_log_snippet": None})()
        with patch("autoforge.agent.cli.find_request_by_seq", return_value=mock_req):
            cmd_build_log(SAMPLE_CAMPAIGN, seq=1)

        captured = capsys.readouterr()
        assert "No build log" in captured.out

    def test_build_log_found(self, capsys: pytest.CaptureFixture) -> None:
        mock_req = type("Req", (), {"build_log_snippet": "error: bad thing\nok line"})()
        with patch("autoforge.agent.cli.find_request_by_seq", return_value=mock_req):
            cmd_build_log(SAMPLE_CAMPAIGN, seq=1)

        captured = capsys.readouterr()
        assert ">>> error: bad thing" in captured.out
        assert "    ok line" in captured.out


class TestCmdHints:
    def test_no_arch(self, capsys: pytest.CaptureFixture) -> None:
        campaign: dict = {"campaign": {"name": "test"}}
        with pytest.raises(SystemExit):
            cmd_hints(campaign, arch_override=None)
        assert "No arch specified" in capsys.readouterr().out

    def test_unknown_arch(self, capsys: pytest.CaptureFixture) -> None:
        campaign: dict = {"platform": {"arch": "mips64"}}
        with pytest.raises(SystemExit):
            cmd_hints(campaign, arch_override=None)
        assert "Unknown arch" in capsys.readouterr().out

    def test_summary(self, capsys: pytest.CaptureFixture) -> None:
        campaign: dict = {"platform": {"arch": "ppc64le"}}
        cmd_hints(campaign, arch_override=None)
        out = capsys.readouterr().out
        assert "ppc64le" in out
        assert "optimization" in out

    def test_arch_override(self, capsys: pytest.CaptureFixture) -> None:
        campaign: dict = {"platform": {"arch": "ppc64le"}}
        cmd_hints(campaign, arch_override="x86_64")
        assert "x86_64" in capsys.readouterr().out

    def test_list_topics(self, capsys: pytest.CaptureFixture) -> None:
        campaign: dict = {"platform": {"arch": "ppc64le"}}
        cmd_hints(campaign, arch_override=None, show_topics=True)
        out = capsys.readouterr().out
        assert "optimization" in out
        assert "perf-counters" in out

    def test_perf_counters_topic(self, capsys: pytest.CaptureFixture) -> None:
        campaign: dict = {"platform": {"arch": "ppc64le"}}
        cmd_hints(campaign, arch_override=None, topic="perf-counters")
        out = capsys.readouterr().out
        assert "perf-counters" in out
        assert "ppc64le-perf-counters.md" in out
