"""Tests for loop module utility functions."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from src.agent.autonomous import _below_threshold
from src.agent.loop import run_baseline


class TestBelowThreshold:
    def test_below_returns_true(self) -> None:
        campaign = {"metric": {"threshold": 0.5}}
        assert _below_threshold(10.1, 10.0, campaign) is True

    def test_above_returns_false(self) -> None:
        campaign = {"metric": {"threshold": 0.01}}
        assert _below_threshold(11.0, 10.0, campaign) is False

    def test_no_threshold_returns_false(self) -> None:
        campaign = {"metric": {}}
        assert _below_threshold(10.0, 10.0, campaign) is False

    def test_none_metric_returns_false(self) -> None:
        campaign = {"metric": {"threshold": 0.5}}
        assert _below_threshold(None, 10.0, campaign) is False

    def test_none_best_returns_false(self) -> None:
        campaign = {"metric": {"threshold": 0.5}}
        assert _below_threshold(10.0, None, campaign) is False

    def test_exact_threshold_returns_false(self) -> None:
        campaign = {"metric": {"threshold": 1.0}}
        assert _below_threshold(11.0, 10.0, campaign) is False


SAMPLE_CAMPAIGN = {
    "campaign": {"name": "test", "max_iterations": 50},
    "metric": {"name": "throughput_mpps", "path": "throughput_mpps", "direction": "maximize"},
    "test": {"backend": "testpmd", "perf": True, "test_suites": ["TestPmd"]},
    "agent": {"poll_interval": 5, "timeout_minutes": 1},
    "dpdk": {"submodule_path": "dpdk", "optimization_branch": "autosearch/optimize"},
}


class TestRunBaseline:
    def test_dry_run_creates_request(self, tmp_path: Path) -> None:
        requests_dir = tmp_path / "requests"
        fake_commit = "abc123def456"

        with (
            patch("src.agent.loop.git_submodule_head", return_value=fake_commit),
            patch("src.agent.loop.next_sequence", return_value=1),
            patch("src.agent.loop.create_request", wraps=None) as mock_create,
            patch("src.agent.loop.git_add_commit_push") as mock_git,
        ):
            # Make create_request write a real file so we can inspect it
            from src.agent.protocol import create_request as real_create

            def fake_create(seq, commit, campaign, description, requests_dir=None):
                return real_create(seq, commit, campaign, description, requests_dir=requests_dir)

            mock_create.side_effect = lambda seq, commit, campaign, desc: fake_create(
                seq, commit, campaign, desc, requests_dir=requests_dir
            )

            run_baseline(SAMPLE_CAMPAIGN, tmp_path / "dpdk", dry_run=True)

        mock_create.assert_called_once_with(
            1, fake_commit, SAMPLE_CAMPAIGN, "Baseline: unmodified DPDK"
        )
        mock_git.assert_called_once()
        _, kwargs = mock_git.call_args
        assert kwargs["dry_run"] is True

        # Verify the request file contents
        request_files = list(requests_dir.glob("0001_*.json"))
        assert len(request_files) == 1
        data = json.loads(request_files[0].read_text())
        assert data["dpdk_commit"] == fake_commit
        assert data["perf"] is True
        assert data["status"] == "pending"
        assert data["backend"] == "testpmd"
        assert data["description"] == "Baseline: unmodified DPDK"

    def test_dry_run_does_not_poll(self, tmp_path: Path) -> None:
        with (
            patch("src.agent.loop.git_submodule_head", return_value="abc123"),
            patch("src.agent.loop.next_sequence", return_value=1),
            patch("src.agent.loop.create_request") as mock_create,
            patch("src.agent.loop.git_add_commit_push"),
            patch("src.agent.loop.poll_for_completion") as mock_poll,
        ):
            mock_create.return_value = tmp_path / "requests" / "0001_test.json"
            run_baseline(SAMPLE_CAMPAIGN, tmp_path / "dpdk", dry_run=True)

        mock_poll.assert_not_called()

    def test_only_stages_request_file(self, tmp_path: Path) -> None:
        with (
            patch("src.agent.loop.git_submodule_head", return_value="abc123"),
            patch("src.agent.loop.next_sequence", return_value=1),
            patch("src.agent.loop.create_request") as mock_create,
            patch("src.agent.loop.git_add_commit_push") as mock_git,
        ):
            mock_create.return_value = tmp_path / "requests" / "0001_test.json"
            run_baseline(SAMPLE_CAMPAIGN, tmp_path / "dpdk", dry_run=True)

        staged_paths = mock_git.call_args[0][0]
        assert len(staged_paths) == 1
        assert "dpdk" not in staged_paths[0]
