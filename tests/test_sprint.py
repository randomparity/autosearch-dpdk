"""Tests for sprint lifecycle management."""

from __future__ import annotations

import csv
from pathlib import Path
from unittest.mock import patch

import pytest

from autoforge.agent.sprint import (
    init_sprint,
    list_sprints,
    switch_sprint,
    validate_sprint_name,
)


def _patch_pointer(sprints_dir: Path, project: str = "test"):
    """Patch _sprints_root_from_pointer and load_pointer for tests."""
    pointer = {"project": project, "sprint": "2026-01-01-dummy"}
    return (
        patch(
            "autoforge.agent.sprint._sprints_root_from_pointer",
            return_value=(sprints_dir, project),
        ),
        patch("autoforge.agent.sprint.load_pointer", return_value=pointer),
        patch("autoforge.agent.sprint.save_pointer"),
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
        template = tmp_path / "campaign.toml.example"
        template.write_text('[campaign]\nname = "test"\n')
        sprints_dir = tmp_path / "sprints"

        p1, p2, p3 = _patch_pointer(sprints_dir)
        with p1, p2, p3:
            sdir = init_sprint("2026-03-25-test", template=template)

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
        template = tmp_path / "campaign.toml.example"
        template.write_text('[campaign]\nname = "test"\n')
        sprints_dir = tmp_path / "sprints"

        p1, p2, p3 = _patch_pointer(sprints_dir)
        with p1, p2, p3:
            init_sprint("2026-03-25-test", template=template)
            with pytest.raises(FileExistsError, match="already exists"):
                init_sprint("2026-03-25-test", template=template)

    def test_invalid_name_raises(self) -> None:
        with pytest.raises(ValueError, match="Must match"):
            init_sprint("BAD_NAME")

    def test_updates_pointer(self, tmp_path: Path) -> None:
        template = tmp_path / "campaign.toml.example"
        template.write_text('[campaign]\nname = "test"\n')
        sprints_dir = tmp_path / "sprints"

        p1, p2, p3 = _patch_pointer(sprints_dir)
        with p1, p2, p3 as mock_save:
            init_sprint("2026-03-25-test", template=template)

        mock_save.assert_called_once_with("test", "2026-03-25-test")

    def test_from_sprint(self, tmp_path: Path) -> None:
        sprints_dir = tmp_path / "sprints"
        source = sprints_dir / "2026-03-24-source"
        source.mkdir(parents=True)
        (source / "campaign.toml").write_text('[campaign]\nname = "cloned"\n')

        p1, p2, p3 = _patch_pointer(sprints_dir)
        with p1, p2, p3:
            sdir = init_sprint("2026-03-25-test", from_sprint="2026-03-24-source")

        content = (sdir / "campaign.toml").read_text()
        assert 'name = "cloned"' in content


class TestListSprints:
    def test_no_sprints_dir(self, tmp_path: Path) -> None:
        p1, p2, p3 = _patch_pointer(tmp_path / "nonexistent")
        with p1, p2, p3:
            assert list_sprints() == []

    def test_empty_sprints_dir(self, tmp_path: Path) -> None:
        sprints = tmp_path / "sprints"
        sprints.mkdir()
        p1, p2, p3 = _patch_pointer(sprints)
        with p1, p2, p3:
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

        p1, p2, p3 = _patch_pointer(sprints)
        with p1, p2, p3:
            result = list_sprints()

        assert len(result) == 1
        assert result[0]["name"] == "2026-03-25-test"
        assert result[0]["iterations"] == 2
        assert result[0]["max_metric"] == 90.00

    def test_ignores_non_sprint_dirs(self, tmp_path: Path) -> None:
        sprints = tmp_path / "sprints"
        (sprints / "not-a-sprint").mkdir(parents=True)
        (sprints / "2026-03-25-valid").mkdir(parents=True)

        p1, p2, p3 = _patch_pointer(sprints)
        with p1, p2, p3:
            result = list_sprints()

        assert len(result) == 1
        assert result[0]["name"] == "2026-03-25-valid"


class TestSwitchSprint:
    def test_switch_to_existing(self, tmp_path: Path) -> None:
        sprints = tmp_path / "sprints"
        (sprints / "2026-03-25-test").mkdir(parents=True)

        p1, p2, p3 = _patch_pointer(sprints)
        with p1, p2, p3 as mock_save:
            switch_sprint("2026-03-25-test")

        mock_save.assert_called_once_with("test", "2026-03-25-test")

    def test_switch_to_nonexistent_raises(self, tmp_path: Path) -> None:
        sprints = tmp_path / "sprints"
        sprints.mkdir()

        p1, p2, p3 = _patch_pointer(sprints)
        with p1, p2, p3, pytest.raises(FileNotFoundError, match="Sprint not found"):
            switch_sprint("2026-03-25-missing")
