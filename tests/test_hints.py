"""Tests for architecture-specific optimization hints lookup."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from src.agent.hints import HINTS_DIR, hints_path, hints_summary, resolve_arch


class TestHintsPath:
    def test_valid_arch(self) -> None:
        path = hints_path("ppc64le")
        assert path == HINTS_DIR / "ppc64le.md"
        assert path.exists()

    def test_unknown_arch(self) -> None:
        with pytest.raises(ValueError, match="Unknown arch 'mips64'"):
            hints_path("mips64")

    def test_missing_file(self) -> None:
        with (
            patch("src.agent.hints.HINTS_DIR", Path("/nonexistent/dir")),
            pytest.raises(FileNotFoundError, match="No hints file"),
        ):
            hints_path("aarch64")


class TestHintsSummary:
    def test_format(self) -> None:
        result = hints_summary("ppc64le")
        assert "Architecture hints for ppc64le:" in result
        assert "ppc64le.md" in result
        assert "lines" in result


class TestResolveArch:
    def test_present(self) -> None:
        campaign = {"platform": {"arch": "ppc64le"}}
        assert resolve_arch(campaign) == "ppc64le"

    def test_absent(self) -> None:
        assert resolve_arch({}) is None

    def test_no_platform_section(self) -> None:
        assert resolve_arch({"goal": {}}) is None
