"""Tests for runner service entry point."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from autoforge.runner.base import BuildRunner, DeployRunner, FullRunner, TestRunner
from autoforge.runner.service import PHASE_RUNNERS, load_config


class TestLoadConfig:
    def test_loads_from_explicit_path(self, tmp_path) -> None:
        toml_path = tmp_path / "runner.toml"
        toml_path.write_text('[runner]\nrunner_id = "lab-1"\n')
        config = load_config(str(toml_path))
        assert config["runner"]["runner_id"] == "lab-1"

    def test_loads_from_env_var(self, tmp_path, monkeypatch) -> None:
        toml_path = tmp_path / "env_runner.toml"
        toml_path.write_text("[runner]\npoll_interval = 10\n")
        monkeypatch.setenv("AUTOFORGE_CONFIG", str(toml_path))
        config = load_config()
        assert config["runner"]["poll_interval"] == 10

    def test_missing_file_raises(self) -> None:
        with pytest.raises(FileNotFoundError):
            load_config("/nonexistent/path/runner.toml")


class TestPhaseRunners:
    def test_contains_all_four_phases(self) -> None:
        assert set(PHASE_RUNNERS) == {"all", "build", "deploy", "test"}

    def test_all_maps_to_full_runner(self) -> None:
        assert PHASE_RUNNERS["all"] is FullRunner

    def test_build_maps_to_build_runner(self) -> None:
        assert PHASE_RUNNERS["build"] is BuildRunner

    def test_deploy_maps_to_deploy_runner(self) -> None:
        assert PHASE_RUNNERS["deploy"] is DeployRunner

    def test_test_maps_to_test_runner(self) -> None:
        assert PHASE_RUNNERS["test"] is TestRunner


class TestMain:
    @patch("autoforge.runner.service.load_config")
    @patch("autoforge.runner.service.resolve_campaign_path")
    @patch("autoforge.runner.service.load_campaign")
    @patch("autoforge.runner.service.load_pointer")
    @patch("autoforge.runner.service.setup_logging")
    def test_selects_full_runner_for_phase_all(
        self,
        mock_logging,
        mock_pointer,
        mock_campaign,
        mock_resolve,
        mock_config,
    ) -> None:
        mock_config.return_value = {"runner": {"phase": "all"}}
        mock_resolve.return_value = Path("/fake/campaign.toml")
        mock_campaign.return_value = {}
        mock_pointer.return_value = {"project": "dpdk", "sprint": "test"}

        with patch.object(FullRunner, "poll_loop") as mock_poll:
            from autoforge.runner.service import main

            main()
            mock_poll.assert_called_once()

    @patch("autoforge.runner.service.load_config")
    @patch("autoforge.runner.service.resolve_campaign_path")
    @patch("autoforge.runner.service.load_campaign")
    @patch("autoforge.runner.service.load_pointer")
    @patch("autoforge.runner.service.setup_logging")
    def test_selects_build_runner_for_phase_build(
        self,
        mock_logging,
        mock_pointer,
        mock_campaign,
        mock_resolve,
        mock_config,
    ) -> None:
        mock_config.return_value = {"runner": {"phase": "build"}}
        mock_resolve.return_value = Path("/fake/campaign.toml")
        mock_campaign.return_value = {}
        mock_pointer.return_value = {"project": "dpdk", "sprint": "test"}

        with patch.object(BuildRunner, "poll_loop") as mock_poll:
            from autoforge.runner.service import main

            main()
            mock_poll.assert_called_once()
