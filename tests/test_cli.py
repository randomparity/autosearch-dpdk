"""Tests for CLI subcommands."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from src.agent.cli import cmd_sprint_active, cmd_sprint_init, cmd_sprint_list

SAMPLE_CAMPAIGN = {
    "campaign": {"name": "test", "max_iterations": 50},
    "metric": {"name": "throughput_mpps", "path": "throughput_mpps", "direction": "maximize"},
    "test": {"backend": "testpmd", "perf": True},
    "agent": {"poll_interval": 5, "timeout_minutes": 1},
    "dpdk": {"submodule_path": "dpdk", "optimization_branch": "autosearch/optimize"},
    "sprint": {"name": "2026-01-01-test"},
}


class TestCmdSprintInit:
    def test_success(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        campaign_toml = tmp_path / "campaign.toml"
        campaign_toml.write_text('[campaign]\nname = "test"\n')

        with patch("src.agent.cli.init_sprint") as mock_init:
            mock_init.return_value = tmp_path / "sprints" / "2026-03-25-test"
            cmd_sprint_init("2026-03-25-test", campaign_toml)

        captured = capsys.readouterr()
        assert "Sprint initialized" in captured.out
        mock_init.assert_called_once_with("2026-03-25-test", campaign_toml)

    def test_duplicate_exits(self, tmp_path: Path) -> None:
        campaign_toml = tmp_path / "campaign.toml"
        campaign_toml.write_text('[campaign]\nname = "test"\n')

        with (
            patch("src.agent.cli.init_sprint", side_effect=FileExistsError("already exists")),
            pytest.raises(SystemExit, match="1"),
        ):
            cmd_sprint_init("2026-03-25-test", campaign_toml)

    def test_invalid_name_exits(self, tmp_path: Path) -> None:
        campaign_toml = tmp_path / "campaign.toml"
        campaign_toml.write_text('[campaign]\nname = "test"\n')

        with (
            patch("src.agent.cli.init_sprint", side_effect=ValueError("Must match")),
            pytest.raises(SystemExit, match="1"),
        ):
            cmd_sprint_init("BAD", campaign_toml)


class TestCmdSprintList:
    def test_no_sprints(self, capsys: pytest.CaptureFixture) -> None:
        with patch("src.agent.cli.list_sprints", return_value=[]):
            cmd_sprint_list(SAMPLE_CAMPAIGN)

        captured = capsys.readouterr()
        assert "No sprints found" in captured.out

    def test_with_sprints(self, capsys: pytest.CaptureFixture) -> None:
        sprints = [
            {"name": "2026-01-01-test", "iterations": 5, "max_metric": 86.25},
            {"name": "2026-02-01-next", "iterations": 0, "max_metric": None},
        ]
        with patch("src.agent.cli.list_sprints", return_value=sprints):
            cmd_sprint_list(SAMPLE_CAMPAIGN)

        captured = capsys.readouterr()
        assert "2026-01-01-test" in captured.out
        assert "86.25" in captured.out
        assert "(active)" in captured.out
        assert "no data" in captured.out


class TestCmdSprintActive:
    def test_active_sprint(self, capsys: pytest.CaptureFixture) -> None:
        cmd_sprint_active(SAMPLE_CAMPAIGN)
        captured = capsys.readouterr()
        assert "2026-01-01-test" in captured.out

    def test_no_active_sprint(self) -> None:
        campaign = {"campaign": {"name": "test"}}
        with pytest.raises(SystemExit, match="1"):
            cmd_sprint_active(campaign)
