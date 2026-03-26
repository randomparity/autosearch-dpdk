"""Tests for runner base classes and utility functions."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from autoforge.agent.protocol import create_request
from autoforge.plugins.protocols import BuildResult, DeployResult, TestResult
from autoforge.protocol import (
    STATUS_BUILDING,
    STATUS_BUILT,
    STATUS_COMPLETED,
    STATUS_DEPLOYED,
    STATUS_DEPLOYING,
    STATUS_RUNNING,
    TestRequest,
)
from autoforge.runner.base import (
    BuildRunner,
    DeployRunner,
    FullRunner,
    TestRunner,
    git_pull,
    recover_stale_requests,
)

SAMPLE_CAMPAIGN = {
    "metric": {
        "name": "throughput_mpps",
        "path": "results.throughput_mpps",
    },
    "project": {
        "name": "dpdk",
        "build": "local",
        "deploy": "local",
        "test": "testpmd-memif",
    },
}

SAMPLE_CONFIG = {
    "runner": {"runner_id": "test-runner", "poll_interval": "5"},
    "paths": {"dpdk_src": "/opt/dpdk", "build_dir": "/tmp/dpdk-build"},
    "timeouts": {"build_minutes": "1", "test_minutes": "1"},
}


def _make_request(
    tmp_path: Path,
    seq: int = 1,
    status: str = "pending",
) -> tuple[TestRequest, Path]:
    path = create_request(seq, "abc123", SAMPLE_CAMPAIGN, "test change", tmp_path)
    req = TestRequest.read(path)
    if status != "pending":
        req.status = status
        req.write(path)
    return req, path


class TestGitPull:
    @patch("autoforge.runner.base.subprocess.run")
    def test_success_returns_true(self, mock_run) -> None:
        mock_run.return_value = subprocess.CompletedProcess([], 0, stdout="", stderr="")
        assert git_pull() is True
        mock_run.assert_called_once()

    @patch("autoforge.runner.base.subprocess.run")
    def test_failure_returns_false(self, mock_run) -> None:
        mock_run.return_value = subprocess.CompletedProcess([], 1, stdout="", stderr="error")
        assert git_pull() is False


class TestRecoverStaleRequests:
    def test_nonexistent_dir_is_noop(self, tmp_path) -> None:
        recover_stale_requests(tmp_path / "nonexistent", frozenset({"claimed"}))

    def test_empty_dir_is_noop(self, tmp_path) -> None:
        recover_stale_requests(tmp_path, frozenset({"claimed"}))

    @patch("autoforge.runner.base.fail")
    def test_recovers_matching_stale_status(self, mock_fail, tmp_path) -> None:
        _make_request(tmp_path, seq=1, status="claimed")
        recover_stale_requests(tmp_path, frozenset({"claimed"}))
        assert mock_fail.call_count == 1
        call_args = mock_fail.call_args
        assert call_args[0][0].sequence == 1
        assert call_args[1]["error"] == "runner restarted"

    @patch("autoforge.runner.base.fail")
    def test_recovers_multiple_stale_statuses(self, mock_fail, tmp_path) -> None:
        _make_request(tmp_path, seq=1, status="claimed")
        _make_request(tmp_path, seq=2, status="building")
        recover_stale_requests(tmp_path, frozenset({"claimed", "building"}))
        assert mock_fail.call_count == 2

    @patch("autoforge.runner.base.fail")
    def test_skips_non_matching_status(self, mock_fail, tmp_path) -> None:
        _make_request(tmp_path, seq=1, status="pending")
        recover_stale_requests(tmp_path, frozenset({"claimed"}))
        mock_fail.assert_not_called()

    @patch("autoforge.runner.base.fail")
    def test_skips_malformed_json(self, mock_fail, tmp_path) -> None:
        (tmp_path / "0001_bad.json").write_text("not json {{{")
        recover_stale_requests(tmp_path, frozenset({"claimed"}))
        mock_fail.assert_not_called()


class TestPhaseRunnerInit:
    def test_extracts_runner_id_and_poll_interval(self, tmp_path) -> None:
        runner = BuildRunner(config=SAMPLE_CONFIG, campaign=SAMPLE_CAMPAIGN, requests_dir=tmp_path)
        assert runner.runner_id == "test-runner"
        assert runner.poll_interval == 5

    def test_defaults_when_missing(self, tmp_path) -> None:
        runner = BuildRunner(config={}, campaign=SAMPLE_CAMPAIGN, requests_dir=tmp_path)
        assert runner.runner_id == ""
        assert runner.poll_interval == 30


def _mock_builder(success: bool = True):
    builder = MagicMock()
    builder.build.return_value = BuildResult(
        success=success,
        log="build log",
        duration_seconds=10.0,
        artifacts={"build_dir": "/tmp/dpdk-build"},
    )
    return builder


def _mock_deployer(success: bool = True):
    deployer = MagicMock()
    deployer.deploy.return_value = DeployResult(
        success=success,
        error=None if success else "deploy error",
    )
    return deployer


def _mock_tester(success: bool = True):
    tester = MagicMock()
    tester.test.return_value = TestResult(
        success=success,
        metric_value=86.0 if success else None,
        results_json={"throughput": 86.0} if success else None,
        results_summary="86.0 Mpps" if success else None,
        error=None if success else "test error",
        duration_seconds=10.0,
    )
    return tester


class TestBuildRunnerExecutePhase:
    @patch("autoforge.runner.base.update_status")
    @patch("autoforge.runner.base.fail")
    def test_success_transitions_to_built(self, mock_fail, mock_update, tmp_path) -> None:
        req, path = _make_request(tmp_path, status="claimed")
        builder = _mock_builder(success=True)
        with patch("autoforge.runner.base.load_component", return_value=builder):
            runner = BuildRunner(
                config=SAMPLE_CONFIG,
                campaign=SAMPLE_CAMPAIGN,
                requests_dir=tmp_path,
            )
            runner.execute_phase(req, path)
        statuses = [call[0][1] for call in mock_update.call_args_list]
        assert STATUS_BUILDING in statuses
        assert STATUS_BUILT in statuses
        mock_fail.assert_not_called()

    @patch("autoforge.runner.base.update_status")
    @patch("autoforge.runner.base.fail")
    def test_failure_calls_fail(self, mock_fail, mock_update, tmp_path) -> None:
        req, path = _make_request(tmp_path, status="claimed")
        builder = _mock_builder(success=False)
        with patch("autoforge.runner.base.load_component", return_value=builder):
            runner = BuildRunner(
                config=SAMPLE_CONFIG,
                campaign=SAMPLE_CAMPAIGN,
                requests_dir=tmp_path,
            )
            runner.execute_phase(req, path)
        mock_fail.assert_called_once()
        assert "Build failed" in mock_fail.call_args[1]["error"]


class TestDeployRunnerExecutePhase:
    @patch("autoforge.runner.base.update_status")
    @patch("autoforge.runner.base.fail")
    def test_success_transitions_to_deployed(self, mock_fail, mock_update, tmp_path) -> None:
        req, path = _make_request(tmp_path, status="built")
        deployer = _mock_deployer(success=True)
        with patch("autoforge.runner.base.load_component", return_value=deployer):
            runner = DeployRunner(
                config=SAMPLE_CONFIG,
                campaign=SAMPLE_CAMPAIGN,
                requests_dir=tmp_path,
            )
            runner.execute_phase(req, path)
        statuses = [call[0][1] for call in mock_update.call_args_list]
        assert STATUS_DEPLOYING in statuses
        assert STATUS_DEPLOYED in statuses
        mock_fail.assert_not_called()

    @patch("autoforge.runner.base.update_status")
    @patch("autoforge.runner.base.fail")
    def test_failure_calls_fail(self, mock_fail, mock_update, tmp_path) -> None:
        req, path = _make_request(tmp_path, status="built")
        deployer = _mock_deployer(success=False)
        with patch("autoforge.runner.base.load_component", return_value=deployer):
            runner = DeployRunner(
                config=SAMPLE_CONFIG,
                campaign=SAMPLE_CAMPAIGN,
                requests_dir=tmp_path,
            )
            runner.execute_phase(req, path)
        mock_fail.assert_called_once()


class TestTestRunnerExecutePhase:
    @patch("autoforge.runner.base.update_status")
    @patch("autoforge.runner.base.fail")
    def test_success_transitions_to_completed(self, mock_fail, mock_update, tmp_path) -> None:
        req, path = _make_request(tmp_path, status="deployed")
        tester = _mock_tester(success=True)
        with patch("autoforge.runner.base.load_component", return_value=tester):
            runner = TestRunner(
                config=SAMPLE_CONFIG,
                campaign=SAMPLE_CAMPAIGN,
                requests_dir=tmp_path,
            )
            runner.execute_phase(req, path)
        statuses = [call[0][1] for call in mock_update.call_args_list]
        assert STATUS_RUNNING in statuses
        assert STATUS_COMPLETED in statuses
        mock_fail.assert_not_called()
        completed_call = [c for c in mock_update.call_args_list if c[0][1] == STATUS_COMPLETED][0]
        assert completed_call[1]["metric_value"] == 86.0

    @patch("autoforge.runner.base.update_status")
    @patch("autoforge.runner.base.fail")
    def test_failure_calls_fail(self, mock_fail, mock_update, tmp_path) -> None:
        req, path = _make_request(tmp_path, status="deployed")
        tester = _mock_tester(success=False)
        with patch("autoforge.runner.base.load_component", return_value=tester):
            runner = TestRunner(
                config=SAMPLE_CONFIG,
                campaign=SAMPLE_CAMPAIGN,
                requests_dir=tmp_path,
            )
            runner.execute_phase(req, path)
        mock_fail.assert_called_once()


class TestFullRunnerExecutePhase:
    @patch("autoforge.runner.base.update_status")
    @patch("autoforge.runner.base.fail")
    def test_full_pipeline_success(self, mock_fail, mock_update, tmp_path) -> None:
        req, path = _make_request(tmp_path, status="claimed")
        builder = _mock_builder(success=True)
        deployer = _mock_deployer(success=True)
        tester = _mock_tester(success=True)

        def load_side_effect(_proj, category, _name, **_kw):
            return {"build": builder, "deploy": deployer, "test": tester}[category]

        with patch("autoforge.runner.base.load_component", side_effect=load_side_effect):
            runner = FullRunner(
                config=SAMPLE_CONFIG,
                campaign=SAMPLE_CAMPAIGN,
                requests_dir=tmp_path,
            )
            runner.execute_phase(req, path)

        statuses = [call[0][1] for call in mock_update.call_args_list]
        assert STATUS_BUILDING in statuses
        assert STATUS_BUILT in statuses
        assert STATUS_DEPLOYING in statuses
        assert STATUS_DEPLOYED in statuses
        assert STATUS_RUNNING in statuses
        assert STATUS_COMPLETED in statuses
        mock_fail.assert_not_called()

    @patch("autoforge.runner.base.update_status")
    @patch("autoforge.runner.base.fail")
    def test_build_failure_short_circuits(self, mock_fail, mock_update, tmp_path) -> None:
        req, path = _make_request(tmp_path, status="claimed")
        builder = _mock_builder(success=False)

        with patch("autoforge.runner.base.load_component", return_value=builder):
            runner = FullRunner(
                config=SAMPLE_CONFIG,
                campaign=SAMPLE_CAMPAIGN,
                requests_dir=tmp_path,
            )
            runner.execute_phase(req, path)

        mock_fail.assert_called_once()
        statuses = [call[0][1] for call in mock_update.call_args_list]
        assert STATUS_COMPLETED not in statuses

    @patch("autoforge.runner.base.update_status")
    @patch("autoforge.runner.base.fail")
    def test_deploy_failure_short_circuits(self, mock_fail, mock_update, tmp_path) -> None:
        req, path = _make_request(tmp_path, status="claimed")
        builder = _mock_builder(success=True)
        deployer = _mock_deployer(success=False)

        def load_side_effect(_proj, category, _name, **_kw):
            return {"build": builder, "deploy": deployer}[category]

        with patch("autoforge.runner.base.load_component", side_effect=load_side_effect):
            runner = FullRunner(
                config=SAMPLE_CONFIG,
                campaign=SAMPLE_CAMPAIGN,
                requests_dir=tmp_path,
            )
            runner.execute_phase(req, path)

        mock_fail.assert_called_once()
        statuses = [call[0][1] for call in mock_update.call_args_list]
        assert STATUS_COMPLETED not in statuses

    @patch("autoforge.runner.base.update_status")
    @patch("autoforge.runner.base.fail")
    def test_test_failure_short_circuits(self, mock_fail, mock_update, tmp_path) -> None:
        req, path = _make_request(tmp_path, status="claimed")
        builder = _mock_builder(success=True)
        deployer = _mock_deployer(success=True)
        tester = _mock_tester(success=False)

        def load_side_effect(_proj, category, _name, **_kw):
            return {"build": builder, "deploy": deployer, "test": tester}[category]

        with patch("autoforge.runner.base.load_component", side_effect=load_side_effect):
            runner = FullRunner(
                config=SAMPLE_CONFIG,
                campaign=SAMPLE_CAMPAIGN,
                requests_dir=tmp_path,
            )
            runner.execute_phase(req, path)

        mock_fail.assert_called_once()
        statuses = [call[0][1] for call in mock_update.call_args_list]
        assert STATUS_COMPLETED not in statuses
