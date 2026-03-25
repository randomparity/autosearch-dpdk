"""Tests for campaign config resolution and pointer management."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from autoforge.agent.campaign import (
    load_campaign,
    load_pointer,
    resolve_campaign_path,
    save_pointer,
)


class TestLoadPointer:
    def test_reads_pointer(self, tmp_path: Path) -> None:
        f = tmp_path / ".autoforge.toml"
        f.write_text('project = "dpdk"\nsprint = "2026-03-25-test"\n')

        result = load_pointer(f)
        assert result["project"] == "dpdk"
        assert result["sprint"] == "2026-03-25-test"

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_pointer(tmp_path / "missing.toml")

    def test_missing_fields_raises(self, tmp_path: Path) -> None:
        f = tmp_path / ".autoforge.toml"
        f.write_text('project = "dpdk"\n')

        with pytest.raises(KeyError, match="Missing"):
            load_pointer(f)


class TestSavePointer:
    def test_writes_pointer(self, tmp_path: Path) -> None:
        f = tmp_path / ".autoforge.toml"
        save_pointer("dpdk", "2026-03-25-test", f)

        content = f.read_text()
        assert 'project = "dpdk"' in content
        assert 'sprint = "2026-03-25-test"' in content

    def test_overwrites_existing(self, tmp_path: Path) -> None:
        f = tmp_path / ".autoforge.toml"
        f.write_text('project = "old"\nsprint = "old"\n')

        save_pointer("new-proj", "2026-04-01-new", f)

        content = f.read_text()
        assert 'project = "new-proj"' in content
        assert 'sprint = "2026-04-01-new"' in content


class TestResolveCampaignPath:
    def test_explicit_path(self, tmp_path: Path) -> None:
        f = tmp_path / "campaign.toml"
        f.write_text('[campaign]\nname = "test"\n')

        result = resolve_campaign_path(f)
        assert result == f

    def test_explicit_path_missing_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError, match="Campaign config not found"):
            resolve_campaign_path(tmp_path / "missing.toml")

    def test_env_var(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        f = tmp_path / "campaign.toml"
        f.write_text('[campaign]\nname = "env"\n')
        monkeypatch.setenv("AUTOFORGE_CAMPAIGN", str(f))

        result = resolve_campaign_path()
        assert result == f

    def test_env_var_missing_raises(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AUTOFORGE_CAMPAIGN", str(tmp_path / "missing.toml"))
        with pytest.raises(FileNotFoundError, match="AUTOFORGE_CAMPAIGN"):
            resolve_campaign_path()

    def test_pointer_fallback(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("AUTOFORGE_CAMPAIGN", raising=False)

        # Set up sprint dir with campaign.toml
        sprint_dir = tmp_path / "projects" / "dpdk" / "sprints" / "2026-03-25-test"
        sprint_dir.mkdir(parents=True)
        campaign = sprint_dir / "campaign.toml"
        campaign.write_text('[campaign]\nname = "pointer"\n')

        pointer = {"project": "dpdk", "sprint": "2026-03-25-test"}
        with (
            patch("autoforge.agent.campaign.load_pointer", return_value=pointer),
            patch("autoforge.agent.campaign.REPO_ROOT", tmp_path),
        ):
            result = resolve_campaign_path()

        assert result == campaign

    def test_explicit_overrides_env(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        explicit = tmp_path / "explicit.toml"
        explicit.write_text('[campaign]\nname = "explicit"\n')
        env_file = tmp_path / "env.toml"
        env_file.write_text('[campaign]\nname = "env"\n')
        monkeypatch.setenv("AUTOFORGE_CAMPAIGN", str(env_file))

        result = resolve_campaign_path(explicit)
        assert result == explicit


class TestLoadCampaign:
    def test_loads_toml(self, tmp_path: Path) -> None:
        f = tmp_path / "campaign.toml"
        f.write_text('[campaign]\nname = "test"\nmax_iterations = 10\n')

        result = load_campaign(f)
        assert result["campaign"]["name"] == "test"
        assert result["campaign"]["max_iterations"] == 10
