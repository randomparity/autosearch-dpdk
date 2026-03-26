"""Tests for architecture-specific optimization hints lookup."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from autoforge.agent.hints import (
    HINTS_DIR,
    hints_file_ref,
    hints_path,
    list_topics,
    workload_hints,
)
from autoforge.campaign import platform_arch


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
            patch("autoforge.agent.hints.HINTS_DIR", Path("/nonexistent/dir")),
            pytest.raises(FileNotFoundError, match="No .* hints"),
        ):
            hints_path("aarch64")

    def test_perf_counters_topic(self) -> None:
        path = hints_path("ppc64le", topic="perf-counters")
        assert path == HINTS_DIR / "ppc64le-perf-counters.md"
        assert path.exists()

    def test_unknown_topic(self) -> None:
        with pytest.raises(ValueError, match="Unknown topic 'bogus'"):
            hints_path("ppc64le", topic="bogus")

    def test_missing_perf_counters(self) -> None:
        with (
            patch("autoforge.agent.hints.HINTS_DIR", Path("/nonexistent/dir")),
            pytest.raises(FileNotFoundError, match="No perf-counters hints"),
        ):
            hints_path("ppc64le", topic="perf-counters")


class TestHintsSummary:
    def test_format(self) -> None:
        result = hints_file_ref("ppc64le")
        assert "Architecture optimization hints for ppc64le:" in result
        assert "ppc64le.md" in result
        assert "lines" in result

    def test_perf_counters_format(self) -> None:
        result = hints_file_ref("ppc64le", topic="perf-counters")
        assert "perf-counters" in result
        assert "ppc64le-perf-counters.md" in result


class TestListTopics:
    def test_ppc64le_has_both(self) -> None:
        topics = list_topics("ppc64le")
        assert "optimization" in topics
        assert "perf-counters" in topics

    def test_unknown_arch(self) -> None:
        with pytest.raises(ValueError, match="Unknown arch"):
            list_topics("mips64")

    def test_missing_dir(self) -> None:
        with patch("autoforge.agent.hints.HINTS_DIR", Path("/nonexistent/dir")):
            topics = list_topics("ppc64le")
            assert topics == []


class TestWorkloadHints:
    def test_backend_bound_suggestion(self) -> None:
        profile = {"derived_metrics": {"backend_bound": 0.45}, "top_functions": []}
        result = workload_hints("ppc64le", profile)
        assert "Backend-bound" in result
        assert "128B" in result

    def test_l1d_miss_rate_suggestion(self) -> None:
        profile = {"derived_metrics": {"l1d_miss_rate": 0.08}, "top_functions": []}
        result = workload_hints("x86_64", profile)
        assert "L1D cache miss" in result
        assert "64-byte" in result

    def test_low_ipc_suggestion(self) -> None:
        profile = {"derived_metrics": {"ipc": 0.7}, "top_functions": []}
        result = workload_hints("ppc64le", profile)
        assert "IPC is low" in result

    def test_memcpy_hotspot(self) -> None:
        profile = {
            "derived_metrics": {},
            "top_functions": [{"name": "rte_memcpy_func", "pct": 25.0}],
        }
        result = workload_hints("ppc64le", profile)
        assert "rte_memcpy_func" in result
        assert "25.0%" in result

    def test_alloc_hotspot(self) -> None:
        profile = {
            "derived_metrics": {},
            "top_functions": [{"name": "rte_mempool_get_bulk", "pct": 12.0}],
        }
        result = workload_hints("ppc64le", profile)
        assert "rte_mempool_get_bulk" in result
        assert "bulk allocation" in result

    def test_empty_profile_returns_empty(self) -> None:
        result = workload_hints("ppc64le", {})
        assert result == ""

    def test_no_issues_returns_empty(self) -> None:
        profile = {
            "derived_metrics": {"backend_bound": 0.1, "l1d_miss_rate": 0.01, "ipc": 2.5},
            "top_functions": [{"name": "some_function", "pct": 10.0}],
        }
        result = workload_hints("ppc64le", profile)
        assert result == ""


class TestPlatformArch:
    def test_present(self) -> None:
        campaign = {"platform": {"arch": "ppc64le"}}
        assert platform_arch(campaign) == "ppc64le"

    def test_absent(self) -> None:
        assert platform_arch({}) is None

    def test_no_platform_section(self) -> None:
        assert platform_arch({"goal": {}}) is None
