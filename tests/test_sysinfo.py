"""Tests for system info collection module."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from autoforge.agent.sysinfo import (
    VALID_ROLES,
    collect_sysinfo,
    load_all_sysinfo,
    render_sysinfo_section,
    save_sysinfo,
)


class TestCollectSysinfo:
    def test_returns_expected_keys(self) -> None:
        info = collect_sysinfo()
        expected_keys = {
            "hostname",
            "os",
            "kernel",
            "arch",
            "cpu_model",
            "cpu_count_physical",
            "cpu_count_logical",
            "memory_gb",
            "python_version",
            "gpu",
            "compiler",
        }
        assert set(info.keys()) == expected_keys

    def test_values_have_correct_types(self) -> None:
        info = collect_sysinfo()
        assert isinstance(info["hostname"], str)
        assert isinstance(info["os"], str)
        assert isinstance(info["arch"], str)
        assert isinstance(info["python_version"], str)
        assert isinstance(info["gpu"], list)
        assert isinstance(info["compiler"], str)
        assert info["cpu_count_logical"] is None or isinstance(info["cpu_count_logical"], int)
        assert info["memory_gb"] is None or isinstance(info["memory_gb"], (int, float))

    def test_hostname_not_empty(self) -> None:
        info = collect_sysinfo()
        assert info["hostname"]

    def test_arch_not_empty(self) -> None:
        info = collect_sysinfo()
        assert info["arch"]


class TestSaveSysinfo:
    def test_writes_valid_json(self, tmp_path: Path) -> None:
        path = save_sysinfo("agent", tmp_path)
        assert path.exists()
        data = json.loads(path.read_text())
        assert data["role"] == "agent"
        assert "collected_at" in data
        assert "hostname" in data

    def test_filename_includes_role(self, tmp_path: Path) -> None:
        path = save_sysinfo("build", tmp_path)
        assert path.name == "sysinfo-build.json"

    def test_overwrites_existing(self, tmp_path: Path) -> None:
        save_sysinfo("test", tmp_path)
        path = save_sysinfo("test", tmp_path)
        data = json.loads(path.read_text())
        assert data["role"] == "test"

    def test_creates_output_dir(self, tmp_path: Path) -> None:
        nested = tmp_path / "a" / "b" / "c"
        path = save_sysinfo("runner", nested)
        assert path.exists()

    def test_invalid_role_raises(self, tmp_path: Path) -> None:
        import pytest

        with pytest.raises(ValueError, match="Invalid role"):
            save_sysinfo("invalid", tmp_path)

    def test_all_valid_roles(self, tmp_path: Path) -> None:
        for role in VALID_ROLES:
            path = save_sysinfo(role, tmp_path)
            assert path.exists()


class TestLoadAllSysinfo:
    def test_round_trip(self, tmp_path: Path) -> None:
        save_sysinfo("agent", tmp_path)
        save_sysinfo("runner", tmp_path)
        loaded = load_all_sysinfo(tmp_path)
        assert "agent" in loaded
        assert "runner" in loaded
        assert loaded["agent"]["role"] == "agent"
        assert loaded["runner"]["role"] == "runner"

    def test_empty_dir(self, tmp_path: Path) -> None:
        assert load_all_sysinfo(tmp_path) == {}

    def test_ignores_corrupt_files(self, tmp_path: Path) -> None:
        save_sysinfo("agent", tmp_path)
        (tmp_path / "sysinfo-bad.json").write_text("not json{{{")
        loaded = load_all_sysinfo(tmp_path)
        assert "agent" in loaded
        assert "bad" not in loaded


class TestRenderSysinfoSection:
    def test_empty_shows_hint(self) -> None:
        result = render_sysinfo_section({})
        assert "autoforge sysinfo" in result

    def test_single_role(self) -> None:
        info = {
            "runner": {
                "hostname": "lab01",
                "os": "Linux 6.1",
                "kernel": "6.1.0",
                "arch": "x86_64",
                "cpu_model": "Xeon",
                "cpu_count_physical": 16,
                "cpu_count_logical": 32,
                "memory_gb": 64.0,
                "gpu": [],
                "compiler": "gcc 13",
                "python_version": "3.13.0",
            }
        }
        result = render_sysinfo_section(info)
        assert "## System Info" in result
        assert "All phases run on the same host" in result
        assert "lab01" in result
        assert "Xeon" in result

    def test_multiple_roles_different_hosts(self) -> None:
        base = {
            "os": "Linux 6.1",
            "kernel": "6.1.0",
            "arch": "x86_64",
            "cpu_model": "Xeon",
            "cpu_count_physical": 8,
            "cpu_count_logical": 16,
            "memory_gb": 32.0,
            "gpu": [],
            "compiler": "gcc 13",
            "python_version": "3.13.0",
        }
        info = {
            "build": {**base, "hostname": "build-host"},
            "test": {**base, "hostname": "test-host"},
        }
        result = render_sysinfo_section(info)
        assert "Build" in result
        assert "Test" in result
        assert "same host" not in result

    def test_gpu_list_rendered(self) -> None:
        info = {
            "runner": {
                "hostname": "gpu-box",
                "os": "Linux 6.1",
                "kernel": "6.1.0",
                "arch": "x86_64",
                "cpu_model": "Xeon",
                "cpu_count_physical": 8,
                "cpu_count_logical": 16,
                "memory_gb": 64.0,
                "gpu": ["A100, 80GB, 535.129"],
                "compiler": "gcc 13",
                "python_version": "3.13.0",
            }
        }
        result = render_sysinfo_section(info)
        assert "A100" in result


class TestSummarizeIntegration:
    def test_sysinfo_in_summary(self, tmp_path: Path) -> None:
        """Verify sysinfo renders into generated summary."""
        from autoforge.agent.summarize import generate_summary

        sprint = tmp_path / "projects" / "dpdk" / "sprints" / "2026-01-01-test"
        (sprint / "requests").mkdir(parents=True)
        docs = sprint / "docs"
        docs.mkdir()

        results = sprint / "results.tsv"
        results.write_text(
            "sequence\ttimestamp\tsource_commit\tmetric_value\tstatus\tdescription\ttags\n"
            "1\t2026-01-01T00:00:00\tabc\t10.0\tcompleted\tbaseline\t\n"
        )

        save_sysinfo("runner", docs)

        campaign = {
            "campaign": {"name": "test-opt", "max_iterations": 50},
            "metric": {
                "name": "throughput_mpps",
                "path": "throughput_mpps",
                "direction": "maximize",
            },
            "project": {"name": "dpdk", "scope": ["drivers/net/memif/"]},
            "goal": {"description": "Optimize memif throughput"},
            "platform": {"arch": "ppc64le"},
        }

        pointer = {"project": "dpdk", "sprint": "2026-01-01-test"}
        with (
            patch("autoforge.agent.sprint.REPO_ROOT", tmp_path),
            patch("autoforge.agent.sprint.load_pointer", return_value=pointer),
            patch("autoforge.agent.summarize.REPO_ROOT", tmp_path),
        ):
            text = generate_summary(campaign)

        assert "System Info" in text

    def test_no_sysinfo_shows_hint(self, tmp_path: Path) -> None:
        """Summary without sysinfo files shows the hint comment."""
        from autoforge.agent.summarize import generate_summary

        sprint = tmp_path / "projects" / "dpdk" / "sprints" / "2026-01-01-test"
        (sprint / "requests").mkdir(parents=True)
        (sprint / "docs").mkdir()

        results = sprint / "results.tsv"
        results.write_text(
            "sequence\ttimestamp\tsource_commit\tmetric_value\tstatus\tdescription\ttags\n"
            "1\t2026-01-01T00:00:00\tabc\t10.0\tcompleted\tbaseline\t\n"
        )

        campaign = {
            "campaign": {"name": "test-opt", "max_iterations": 50},
            "metric": {
                "name": "throughput_mpps",
                "path": "throughput_mpps",
                "direction": "maximize",
            },
            "project": {"name": "dpdk", "scope": ["drivers/net/memif/"]},
            "goal": {"description": "Optimize memif throughput"},
            "platform": {"arch": "ppc64le"},
        }

        pointer = {"project": "dpdk", "sprint": "2026-01-01-test"}
        with (
            patch("autoforge.agent.sprint.REPO_ROOT", tmp_path),
            patch("autoforge.agent.sprint.load_pointer", return_value=pointer),
            patch("autoforge.agent.summarize.REPO_ROOT", tmp_path),
        ):
            text = generate_summary(campaign)

        assert "autoforge sysinfo" in text
