"""Tests for sprint lifecycle management."""

from __future__ import annotations

import csv
from pathlib import Path
from unittest.mock import patch

import pytest

from autoforge.agent.sprint import (
    _set_sprint_name,
    init_sprint,
    list_sprints,
    switch_sprint,
    validate_sprint_name,
)


class TestValidateSprintName:
    def test_valid_name(self) -> None:
        validate_sprint_name("2026-03-25-memif-zc")

    def test_valid_name_minimal(self) -> None:
        validate_sprint_name("2026-01-01-a")

    def test_missing_slug(self) -> None:
        with pytest.raises(ValueError, match="Must match"):
            validate_sprint_name("2026-03-25")

    def test_uppercase(self) -> None:
        with pytest.raises(ValueError, match="Must match"):
            validate_sprint_name("2026-03-25-MemifZC")

    def test_no_date(self) -> None:
        with pytest.raises(ValueError, match="Must match"):
            validate_sprint_name("memif-zc")

    def test_empty(self) -> None:
        with pytest.raises(ValueError, match="Must match"):
            validate_sprint_name("")


class TestInitSprint:
    def test_creates_directory_structure(self, tmp_path: Path) -> None:
        campaign_toml = tmp_path / "campaign.toml"
        campaign_toml.write_text('[campaign]\nname = "test"\n')

        with patch("autoforge.agent.sprint.SPRINTS_ROOT", tmp_path / "sprints"):
            sdir = init_sprint("2026-03-25-test", campaign_toml)

        assert sdir.is_dir()
        assert (sdir / "requests").is_dir()
        assert (sdir / "docs").is_dir()
        assert (sdir / "campaign.toml").exists()

        # Results TSV has header
        with open(sdir / "results.tsv", newline="") as f:
            reader = csv.reader(f, delimiter="\t")
            header = next(reader)
        assert header[0] == "sequence"
        assert header[3] == "metric_value"

    def test_duplicate_raises(self, tmp_path: Path) -> None:
        campaign_toml = tmp_path / "campaign.toml"
        campaign_toml.write_text('[campaign]\nname = "test"\n')

        with patch("autoforge.agent.sprint.SPRINTS_ROOT", tmp_path / "sprints"):
            init_sprint("2026-03-25-test", campaign_toml)
            with pytest.raises(FileExistsError, match="already exists"):
                init_sprint("2026-03-25-test", campaign_toml)

    def test_invalid_name_raises(self, tmp_path: Path) -> None:
        campaign_toml = tmp_path / "campaign.toml"
        campaign_toml.write_text('[campaign]\nname = "test"\n')

        with pytest.raises(ValueError, match="Must match"):
            init_sprint("BAD_NAME", campaign_toml)

    def test_sets_sprint_name_in_campaign(self, tmp_path: Path) -> None:
        campaign_toml = tmp_path / "campaign.toml"
        campaign_toml.write_text('[campaign]\nname = "test"\n')

        with patch("autoforge.agent.sprint.SPRINTS_ROOT", tmp_path / "sprints"):
            init_sprint("2026-03-25-test", campaign_toml)

        content = campaign_toml.read_text()
        assert 'name = "2026-03-25-test"' in content


class TestListSprints:
    def test_no_sprints_dir(self, tmp_path: Path) -> None:
        with patch("autoforge.agent.sprint.SPRINTS_ROOT", tmp_path / "nonexistent"):
            assert list_sprints() == []

    def test_empty_sprints_dir(self, tmp_path: Path) -> None:
        sprints = tmp_path / "sprints"
        sprints.mkdir()
        with patch("autoforge.agent.sprint.SPRINTS_ROOT", sprints):
            assert list_sprints() == []

    def test_one_sprint_with_data(self, tmp_path: Path) -> None:
        sprints = tmp_path / "sprints"
        sdir = sprints / "2026-03-25-test"
        sdir.mkdir(parents=True)
        tsv = sdir / "results.tsv"
        header = ["sequence", "timestamp", "source_commit", "metric_value", "status", "description"]
        with open(tsv, "w", newline="") as f:
            writer = csv.writer(f, delimiter="\t")
            writer.writerow(header)
            writer.writerow(["1", "2026-03-25T00:00:00", "abc", "86.25", "completed", "base"])
            writer.writerow(["2", "2026-03-25T00:05:00", "def", "90.00", "completed", "better"])

        with patch("autoforge.agent.sprint.SPRINTS_ROOT", sprints):
            result = list_sprints()

        assert len(result) == 1
        assert result[0]["name"] == "2026-03-25-test"
        assert result[0]["iterations"] == 2
        assert result[0]["max_metric"] == 90.00

    def test_ignores_non_sprint_dirs(self, tmp_path: Path) -> None:
        sprints = tmp_path / "sprints"
        (sprints / "not-a-sprint").mkdir(parents=True)
        (sprints / "2026-03-25-valid").mkdir(parents=True)

        with patch("autoforge.agent.sprint.SPRINTS_ROOT", sprints):
            result = list_sprints()

        assert len(result) == 1
        assert result[0]["name"] == "2026-03-25-valid"


class TestSwitchSprint:
    def test_switch_to_existing(self, tmp_path: Path) -> None:
        sprints = tmp_path / "sprints"
        (sprints / "2026-03-25-test").mkdir(parents=True)
        campaign_toml = tmp_path / "campaign.toml"
        campaign_toml.write_text('[sprint]\nname = "2026-03-24-old"\n')

        with patch("autoforge.agent.sprint.SPRINTS_ROOT", sprints):
            switch_sprint("2026-03-25-test", campaign_toml)

        content = campaign_toml.read_text()
        assert 'name = "2026-03-25-test"' in content

    def test_switch_to_nonexistent_raises(self, tmp_path: Path) -> None:
        sprints = tmp_path / "sprints"
        sprints.mkdir()
        campaign_toml = tmp_path / "campaign.toml"
        campaign_toml.write_text('[sprint]\nname = "2026-03-24-old"\n')

        with (
            patch("autoforge.agent.sprint.SPRINTS_ROOT", sprints),
            pytest.raises(FileNotFoundError, match="Sprint not found"),
        ):
            switch_sprint("2026-03-25-missing", campaign_toml)


class TestSetSprintName:
    def test_existing_sprint_section(self, tmp_path: Path) -> None:
        f = tmp_path / "campaign.toml"
        f.write_text('[sprint]\nname = "old"\n\n[campaign]\nname = "test"\n')

        _set_sprint_name(f, "2026-03-25-new")

        content = f.read_text()
        assert 'name = "2026-03-25-new"' in content
        assert 'name = "old"' not in content
        assert "[campaign]" in content

    def test_no_sprint_section(self, tmp_path: Path) -> None:
        f = tmp_path / "campaign.toml"
        f.write_text('[campaign]\nname = "test"\n')

        _set_sprint_name(f, "2026-03-25-new")

        content = f.read_text()
        assert "[sprint]" in content
        assert 'name = "2026-03-25-new"' in content
        assert "[campaign]" in content

    def test_comment_between_sprint_and_name(self, tmp_path: Path) -> None:
        f = tmp_path / "campaign.toml"
        f.write_text('[sprint]\n# Active sprint\nname = "old"\n\n[campaign]\nname = "test"\n')

        _set_sprint_name(f, "2026-03-25-new")

        content = f.read_text()
        assert 'name = "2026-03-25-new"' in content
        assert 'name = "old"' not in content
        assert "# Active sprint" in content

    def test_blank_lines_between_sprint_and_name(self, tmp_path: Path) -> None:
        f = tmp_path / "campaign.toml"
        f.write_text('[sprint]\n\nname = "old"\n')

        _set_sprint_name(f, "2026-03-25-new")

        content = f.read_text()
        assert 'name = "2026-03-25-new"' in content
        assert 'name = "old"' not in content
