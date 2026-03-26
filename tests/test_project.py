"""Tests for project scaffolding."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from autoforge.agent.project import init_project, validate_project_name


class TestValidateProjectName:
    def test_valid(self) -> None:
        validate_project_name("dpdk")
        validate_project_name("my-project")
        validate_project_name("v2")

    def test_uppercase_rejected(self) -> None:
        with pytest.raises(ValueError, match="lowercase"):
            validate_project_name("MyProject")

    def test_empty_rejected(self) -> None:
        with pytest.raises(ValueError, match="lowercase"):
            validate_project_name("")

    def test_leading_hyphen_rejected(self) -> None:
        with pytest.raises(ValueError, match="lowercase"):
            validate_project_name("-bad")


class TestInitProject:
    def test_creates_skeleton(self, tmp_path: Path) -> None:
        with (
            patch("autoforge.agent.project.REPO_ROOT", tmp_path),
            patch("autoforge.agent.project.save_pointer"),
        ):
            pdir = init_project("vllm")

        assert pdir == tmp_path / "projects" / "vllm"
        assert (pdir / "builds").is_dir()
        assert (pdir / "deploys").is_dir()
        assert (pdir / "tests").is_dir()
        assert (pdir / "perfs").is_dir()
        assert (pdir / "judges").is_dir()
        assert (pdir / "sprints").is_dir()

    def test_duplicate_raises(self, tmp_path: Path) -> None:
        (tmp_path / "projects" / "dpdk").mkdir(parents=True)

        with (
            patch("autoforge.agent.project.REPO_ROOT", tmp_path),
            pytest.raises(FileExistsError, match="already exists"),
        ):
            init_project("dpdk")

    def test_invalid_name_raises(self) -> None:
        with pytest.raises(ValueError, match="lowercase"):
            init_project("BAD_NAME")
