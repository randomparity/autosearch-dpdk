"""Tests for CLI subcommands."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from autoforge.agent.cli import (
    _failure_log,
    _format_inspect,
    _format_log,
    _format_timeline,
    cmd_hints,
    cmd_inspect,
    cmd_logs,
    cmd_revert,
    cmd_sprint_active,
    cmd_sprint_init,
    cmd_sprint_list,
    main,
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
            cmd_sprint_list()

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
            cmd_sprint_list()

        captured = capsys.readouterr()
        assert "2026-01-01-test" in captured.out
        assert "86.25" in captured.out
        assert "(active)" in captured.out
        assert "no data" in captured.out


class TestCmdSprintActive:
    def test_active_sprint(self, capsys: pytest.CaptureFixture) -> None:
        with patch("autoforge.agent.cli.active_sprint_name", return_value="2026-01-01-test"):
            cmd_sprint_active()
        captured = capsys.readouterr()
        assert "2026-01-01-test" in captured.out

    def test_no_active_sprint(self) -> None:
        with (
            patch(
                "autoforge.agent.cli.active_sprint_name",
                side_effect=KeyError("No active sprint"),
            ),
            pytest.raises(SystemExit, match="1"),
        ):
            cmd_sprint_active()


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


class TestFormatLog:
    def test_error_lines_highlighted(self) -> None:
        log = "compiling foo.c\nerror: undefined symbol\nok"
        formatted = _format_log(log)
        assert ">>> error: undefined symbol" in formatted

    def test_fatal_highlighted(self) -> None:
        log = "fatal: something broke"
        assert ">>> fatal:" in _format_log(log)

    def test_normal_lines_indented(self) -> None:
        log = "compiling bar.c"
        assert _format_log(log).startswith("    ")

    def test_deploy_patterns(self) -> None:
        log = "connecting...\ntimeout waiting for service\nrefused"
        from autoforge.agent.cli import _DEPLOY_ERROR_PATTERNS

        formatted = _format_log(log, _DEPLOY_ERROR_PATTERNS)
        assert ">>> timeout" in formatted
        assert ">>> refused" in formatted
        assert "    connecting" in formatted

    def test_test_patterns(self) -> None:
        log = "running tests\nassertionError: expected 5\nFAIL"
        from autoforge.agent.cli import _TEST_ERROR_PATTERNS

        formatted = _format_log(log, _TEST_ERROR_PATTERNS)
        assert ">>> assertion" in formatted
        assert ">>> FAIL" in formatted


class TestCmdLogs:
    def test_logs_not_found(self) -> None:
        with (
            patch("autoforge.agent.cli.find_request_by_seq", return_value=None),
            pytest.raises(SystemExit, match="1"),
        ):
            cmd_logs(SAMPLE_CAMPAIGN, seq=99)

    def test_logs_no_logs_available(self, capsys: pytest.CaptureFixture) -> None:
        from autoforge.protocol import TestRequest

        req = TestRequest(
            sequence=1,
            created_at="2026-01-01T00:00:00",
            source_commit="abc123",
            description="test",
            build_plugin="local",
            deploy_plugin="local",
            test_plugin="testpmd-memif",
        )
        with patch("autoforge.agent.cli.find_request_by_seq", return_value=req):
            cmd_logs(SAMPLE_CAMPAIGN, seq=1)
        assert "No logs available" in capsys.readouterr().out

    def test_logs_auto_detect_phase(self, capsys: pytest.CaptureFixture) -> None:
        from autoforge.protocol import TestRequest

        req = TestRequest(
            sequence=1,
            created_at="2026-01-01T00:00:00",
            source_commit="abc123",
            description="test",
            build_plugin="local",
            deploy_plugin="local",
            test_plugin="testpmd-memif",
            status="failed",
            failed_phase="build",
            build_log_snippet="error: bad\nok line",
        )
        with patch("autoforge.agent.cli.find_request_by_seq", return_value=req):
            cmd_logs(SAMPLE_CAMPAIGN, seq=1)
        out = capsys.readouterr().out
        assert "Build log" in out
        assert ">>> error: bad" in out

    def test_logs_grep_filter(self, capsys: pytest.CaptureFixture) -> None:
        from autoforge.protocol import TestRequest

        req = TestRequest(
            sequence=1,
            created_at="2026-01-01T00:00:00",
            source_commit="abc123",
            description="test",
            build_plugin="local",
            deploy_plugin="local",
            test_plugin="testpmd-memif",
            status="failed",
            failed_phase="build",
            build_log_snippet="line one\nerror: bad\nline three",
        )
        with patch("autoforge.agent.cli.find_request_by_seq", return_value=req):
            cmd_logs(SAMPLE_CAMPAIGN, seq=1, grep="error")
        out = capsys.readouterr().out
        assert "error: bad" in out
        assert "line one" not in out

    def test_logs_tail(self, capsys: pytest.CaptureFixture) -> None:
        from autoforge.protocol import TestRequest

        log = "\n".join(f"line {i}" for i in range(100))
        req = TestRequest(
            sequence=1,
            created_at="2026-01-01T00:00:00",
            source_commit="abc123",
            description="test",
            build_plugin="local",
            deploy_plugin="local",
            test_plugin="testpmd-memif",
            status="failed",
            failed_phase="build",
            build_log_snippet=log,
        )
        with patch("autoforge.agent.cli.find_request_by_seq", return_value=req):
            cmd_logs(SAMPLE_CAMPAIGN, seq=1, tail=5)
        out = capsys.readouterr().out
        assert "5 lines" in out
        assert "line 99" in out
        assert "line 0" not in out

    def test_build_log_alias(self, capsys: pytest.CaptureFixture) -> None:
        """build-log command routes to logs with phase=build."""
        from autoforge.protocol import TestRequest

        req = TestRequest(
            sequence=1,
            created_at="2026-01-01T00:00:00",
            source_commit="abc123",
            description="test",
            build_plugin="local",
            deploy_plugin="local",
            test_plugin="testpmd-memif",
            build_log_snippet="error: bad thing\nok line",
        )
        with patch("autoforge.agent.cli.find_request_by_seq", return_value=req):
            cmd_logs(SAMPLE_CAMPAIGN, seq=1, phase="build")
        out = capsys.readouterr().out
        assert ">>> error: bad thing" in out
        assert "    ok line" in out


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


class TestCmdJudge:
    """Tests for cmd_judge with and without judge plugins."""

    def _base_campaign(self, judge: str | None = None) -> dict:
        project: dict = {
            "name": "dpdk",
            "build": "local",
            "deploy": "local",
            "test": "testpmd-memif",
            "submodule_path": "projects/dpdk/repo",
            "optimization_branch": "autoforge/optimize",
        }
        if judge:
            project["judge"] = judge
        return {
            "campaign": {"name": "test", "max_iterations": 50},
            "metric": {
                "name": "throughput_mpps",
                "path": "throughput_mpps",
                "direction": "maximize",
            },
            "agent": {"poll_interval": 5, "timeout_minutes": 1},
            "project": project,
        }

    def _make_request(self, metric: float | None = 90.0, status: str = "completed"):
        from autoforge.protocol import TestRequest

        req = TestRequest(
            sequence=5,
            created_at="2026-03-26T00:00:00",
            source_commit="abc123",
            description="test change",
            build_plugin="local",
            deploy_plugin="local",
            test_plugin="testpmd-memif",
            profile_plugin="",
            metric_name="throughput_mpps",
            metric_path="throughput_mpps",
        )
        req.status = status
        req.metric_value = metric
        return req

    def test_no_judge_plugin_uses_default(self, tmp_path: Path) -> None:
        from autoforge.agent.cli import cmd_judge

        campaign = self._base_campaign()
        latest = self._make_request()

        with (
            patch("autoforge.agent.cli.check_git_clean"),
            patch("autoforge.agent.cli.requests_dir", return_value=tmp_path / "requests"),
            patch("autoforge.agent.cli.results_path", return_value=tmp_path / "results.tsv"),
            patch("autoforge.agent.cli.failures_path", return_value=tmp_path / "failures.tsv"),
            patch("autoforge.agent.cli.find_latest_request", return_value=latest),
            patch("autoforge.agent.cli.best_result", return_value=None),
            patch("autoforge.agent.cli.append_result"),
            patch("autoforge.agent.cli.apply_judge_verdict") as mock_apply,
        ):
            cmd_judge(campaign, dry_run=True)

        mock_apply.assert_called_once()

    def test_with_judge_plugin_uses_plugin(self, tmp_path: Path, capsys) -> None:
        from autoforge.agent.cli import cmd_judge
        from autoforge.plugins.protocols import JudgeVerdict

        campaign = self._base_campaign(judge="always-keep")
        latest = self._make_request()

        mock_judge = type(
            "FakeJudge",
            (),
            {
                "name": "always-keep",
                "configure": lambda self, *a: None,
                "judge": lambda self, *a, **kw: JudgeVerdict(keep=True, reason="test keep"),
            },
        )()

        with (
            patch("autoforge.agent.cli.check_git_clean"),
            patch("autoforge.agent.cli.requests_dir", return_value=tmp_path / "requests"),
            patch("autoforge.agent.cli.results_path", return_value=tmp_path / "results.tsv"),
            patch("autoforge.agent.cli.failures_path", return_value=tmp_path / "failures.tsv"),
            patch("autoforge.agent.cli.find_latest_request", return_value=latest),
            patch("autoforge.agent.cli.best_result", return_value=None),
            patch("autoforge.agent.cli.append_result"),
            patch("autoforge.agent.judge.load_judge", return_value=mock_judge),
            patch("autoforge.agent.judge.record_verdict"),
        ):
            cmd_judge(campaign, dry_run=True)

        out = capsys.readouterr().out
        assert "always-keep" in out
        assert "keep" in out
        assert "test keep" in out

    def test_rolling_average_mode_calls_rolling_average(self, tmp_path: Path) -> None:
        from autoforge.agent.cli import cmd_judge

        campaign = self._base_campaign()
        campaign["metric"]["comparison"] = "rolling_average"
        campaign["metric"]["comparison_window"] = 3
        latest = self._make_request()

        with (
            patch("autoforge.agent.cli.check_git_clean"),
            patch("autoforge.agent.cli.requests_dir", return_value=tmp_path / "requests"),
            patch("autoforge.agent.cli.results_path", return_value=tmp_path / "results.tsv"),
            patch("autoforge.agent.cli.failures_path", return_value=tmp_path / "failures.tsv"),
            patch("autoforge.agent.cli.find_latest_request", return_value=latest),
            patch("autoforge.agent.cli.rolling_average_result", return_value=85.0) as mock_avg,
            patch("autoforge.agent.cli.best_result") as mock_best,
            patch("autoforge.agent.cli.append_result"),
            patch("autoforge.agent.cli.apply_judge_verdict") as mock_apply,
        ):
            cmd_judge(campaign, dry_run=True)

        mock_avg.assert_called_once_with(
            tmp_path / "results.tsv",
            direction="maximize",
            window=3,
        )
        mock_best.assert_not_called()
        mock_apply.assert_called_once()
        assert mock_apply.call_args[0][1] == 85.0  # best_val from rolling avg


class TestProjectListCommand:
    def test_no_projects(self, capsys: pytest.CaptureFixture) -> None:
        with (
            patch("autoforge.agent.cli.list_projects", return_value=[]),
            patch("autoforge.agent.cli.load_pointer", return_value=SAMPLE_POINTER),
            patch("sys.argv", ["autoforge", "project", "list"]),
        ):
            main()

        assert "No projects found" in capsys.readouterr().out

    def test_lists_projects_with_active_marker(self, capsys: pytest.CaptureFixture) -> None:
        with (
            patch("autoforge.agent.cli.list_projects", return_value=["dpdk", "vllm"]),
            patch("autoforge.agent.cli.load_pointer", return_value=SAMPLE_POINTER),
            patch("sys.argv", ["autoforge", "project", "list"]),
        ):
            main()

        out = capsys.readouterr().out
        assert " * dpdk" in out
        assert "   vllm" in out

    def test_no_active_project_still_lists(self, capsys: pytest.CaptureFixture) -> None:
        with (
            patch("autoforge.agent.cli.list_projects", return_value=["dpdk"]),
            patch("autoforge.agent.cli.load_pointer", side_effect=FileNotFoundError),
            patch("sys.argv", ["autoforge", "project", "list"]),
        ):
            main()

        assert "dpdk" in capsys.readouterr().out


class TestProjectSwitchCommand:
    def test_success_prints_confirmation(self, capsys: pytest.CaptureFixture) -> None:
        with (
            patch("autoforge.agent.cli.switch_project") as mock_switch,
            patch("sys.argv", ["autoforge", "project", "switch", "vllm"]),
        ):
            main()

        mock_switch.assert_called_once_with("vllm")
        assert "Switched to project: vllm" in capsys.readouterr().out

    def test_nonexistent_project_exits(self) -> None:
        with (
            patch(
                "autoforge.agent.cli.switch_project",
                side_effect=FileNotFoundError("Project not found"),
            ),
            patch("sys.argv", ["autoforge", "project", "switch", "nonexistent"]),
            pytest.raises(SystemExit, match="1"),
        ):
            main()

    def test_invalid_name_exits(self) -> None:
        with (
            patch(
                "autoforge.agent.cli.switch_project",
                side_effect=ValueError("Invalid project name"),
            ),
            patch("sys.argv", ["autoforge", "project", "switch", "Bad Name"]),
            pytest.raises(SystemExit, match="1"),
        ):
            main()


def _make_test_request(**overrides):
    """Helper to create a TestRequest for CLI tests."""
    from autoforge.protocol import TestRequest

    defaults = {
        "sequence": 5,
        "created_at": "2026-03-26T12:00:00",
        "source_commit": "abc123def456",
        "description": "test change",
        "build_plugin": "local",
        "deploy_plugin": "local",
        "test_plugin": "testpmd-memif",
    }
    defaults.update(overrides)
    return TestRequest(**defaults)


class TestFormatTimeline:
    def test_basic_timeline(self) -> None:
        req = _make_test_request(
            status="completed",
            claimed_at="2026-03-26T12:01:00",
            built_at="2026-03-26T12:03:00",
            deployed_at="2026-03-26T12:03:30",
            completed_at="2026-03-26T12:05:00",
        )
        timeline = _format_timeline(req)
        assert "created 12:00:00" in timeline
        assert "claimed 12:01:00 (+1m0s)" in timeline
        assert "built 12:03:00 (+2m0s)" in timeline
        assert "deployed 12:03:30 (+30s)" in timeline

    def test_failed_timeline_shows_phase(self) -> None:
        req = _make_test_request(
            status="failed",
            failed_phase="build",
            claimed_at="2026-03-26T12:01:00",
            completed_at="2026-03-26T12:03:00",
        )
        timeline = _format_timeline(req)
        assert "FAILED at build" in timeline

    def test_empty_timeline(self) -> None:
        req = _make_test_request()
        timeline = _format_timeline(req)
        assert "created" in timeline

    def test_missing_intermediate_timestamps(self) -> None:
        req = _make_test_request(
            status="completed",
            completed_at="2026-03-26T12:10:00",
        )
        timeline = _format_timeline(req)
        assert "created" in timeline
        assert "completed" in timeline


class TestFailureLog:
    def test_returns_build_log(self) -> None:
        req = _make_test_request(
            status="failed",
            failed_phase="build",
            build_log_snippet="build error here",
        )
        assert _failure_log(req) == "build error here"

    def test_returns_deploy_log(self) -> None:
        req = _make_test_request(
            status="failed",
            failed_phase="deploy",
            deploy_log_snippet="deploy error here",
        )
        assert _failure_log(req) == "deploy error here"

    def test_returns_test_log(self) -> None:
        req = _make_test_request(
            status="failed",
            failed_phase="test",
            test_log_snippet="test error here",
        )
        assert _failure_log(req) == "test error here"

    def test_fallback_no_phase(self) -> None:
        req = _make_test_request(
            status="failed",
            build_log_snippet="build log",
        )
        assert _failure_log(req) == "build log"

    def test_none_when_no_logs(self) -> None:
        req = _make_test_request(status="failed")
        assert _failure_log(req) is None


class TestFormatInspect:
    def test_basic_inspect(self) -> None:
        req = _make_test_request(
            status="completed",
            metric_value=14.5,
            results_summary="All passed",
            claimed_at="2026-03-26T12:01:00",
            completed_at="2026-03-26T12:05:00",
        )
        output = _format_inspect(req)
        assert "Request 0005" in output
        assert "completed" in output
        assert "14.5" in output
        assert "All passed" in output
        assert "Timeline:" in output

    def test_inspect_with_failure(self) -> None:
        req = _make_test_request(
            status="failed",
            failed_phase="build",
            error="Build failed",
            build_log_snippet="error: something\nok line",
            claimed_at="2026-03-26T12:01:00",
            completed_at="2026-03-26T12:03:00",
        )
        output = _format_inspect(req)
        assert "Failed phase:  build" in output
        assert "Build failed" in output
        assert "Build log" in output

    def test_inspect_with_results_json(self) -> None:
        req = _make_test_request(
            status="completed",
            results_json={"throughput": 14.5},
        )
        output = _format_inspect(req)
        assert "Results JSON:" in output
        assert "throughput" in output

    def test_inspect_truncates_long_logs(self) -> None:
        long_log = "\n".join(f"line {i}" for i in range(100))
        req = _make_test_request(
            status="failed",
            failed_phase="build",
            build_log_snippet=long_log,
        )
        output = _format_inspect(req)
        assert "50 more lines" in output
        assert "logs --seq 5 --phase build" in output


class TestCmdInspect:
    def test_inspect_not_found(self) -> None:
        with (
            patch("autoforge.agent.cli.find_request_by_seq", return_value=None),
            pytest.raises(SystemExit, match="1"),
        ):
            cmd_inspect(SAMPLE_CAMPAIGN, seq=99)

    def test_inspect_json_mode(self, capsys: pytest.CaptureFixture) -> None:
        req = _make_test_request(status="completed", metric_value=14.5)
        with patch("autoforge.agent.cli.find_request_by_seq", return_value=req):
            cmd_inspect(SAMPLE_CAMPAIGN, seq=5, as_json=True)
        import json

        out = capsys.readouterr().out
        data = json.loads(out)
        assert data["sequence"] == 5
        assert data["metric_value"] == 14.5

    def test_inspect_human_mode(self, capsys: pytest.CaptureFixture) -> None:
        req = _make_test_request(status="completed", metric_value=14.5)
        with patch("autoforge.agent.cli.find_request_by_seq", return_value=req):
            cmd_inspect(SAMPLE_CAMPAIGN, seq=5)
        out = capsys.readouterr().out
        assert "Request 0005" in out


class TestFailurePatterns:
    def test_no_requests_dir(self, tmp_path: Path) -> None:
        from autoforge.agent.strategy import format_failure_patterns

        result = format_failure_patterns(tmp_path / "nonexistent")
        assert result == ""

    def test_no_failures(self, tmp_path: Path) -> None:
        from autoforge.agent.strategy import format_failure_patterns

        req_dir = tmp_path / "requests"
        req_dir.mkdir()
        req = _make_test_request(status="completed", metric_value=14.5)
        req.write(req_dir / req.filename)
        result = format_failure_patterns(req_dir)
        assert result == ""

    def test_groups_by_phase(self, tmp_path: Path) -> None:
        from autoforge.agent.strategy import format_failure_patterns

        req_dir = tmp_path / "requests"
        req_dir.mkdir()

        for i, phase in enumerate(["build", "build", "test"], start=1):
            req = _make_test_request(
                sequence=i,
                created_at=f"2026-03-26T12:0{i}:00",
                status="failed",
                failed_phase=phase,
                error=f"{phase} failed",
            )
            req.write(req_dir / req.filename)

        result = format_failure_patterns(req_dir)
        assert "Recent failures:" in result
        assert "2 build" in result
        assert "1 test" in result

    def test_classifies_timeout(self, tmp_path: Path) -> None:
        from autoforge.agent.strategy import format_failure_patterns

        req_dir = tmp_path / "requests"
        req_dir.mkdir()
        req = _make_test_request(
            sequence=1,
            status="failed",
            failed_phase="build",
            error="timeout exceeded",
        )
        req.write(req_dir / req.filename)
        result = format_failure_patterns(req_dir)
        assert "timeout" in result

    def test_classifies_linker(self, tmp_path: Path) -> None:
        from autoforge.agent.strategy import format_failure_patterns

        req_dir = tmp_path / "requests"
        req_dir.mkdir()
        req = _make_test_request(
            sequence=1,
            status="failed",
            failed_phase="build",
            error="Build failed",
            build_log_snippet="undefined reference to `foo`",
        )
        req.write(req_dir / req.filename)
        result = format_failure_patterns(req_dir)
        assert "linker" in result
