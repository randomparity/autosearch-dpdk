"""Tests for DPDK perf-record profiler plugin."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

from autoforge.plugins.protocols import Profiler

PLUGIN_PATH = Path(__file__).parent.parent / "projects" / "dpdk" / "perfs" / "perf-record.py"
MODULE_NAME = "autoforge_plugin_perf_record_test"


def _load_module():
    spec = importlib.util.spec_from_file_location(MODULE_NAME, PLUGIN_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[MODULE_NAME] = mod
    spec.loader.exec_module(mod)
    return mod


_mod = _load_module()
PerfRecordProfiler = _mod.PerfRecordProfiler


class TestConfigure:
    def test_stores_profiling_config(self) -> None:
        p = PerfRecordProfiler()
        p.configure({}, {"profiling": {"frequency": 199, "sudo": False}})
        assert p._config == {"frequency": 199, "sudo": False}

    def test_missing_profiling_section_defaults_empty(self) -> None:
        p = PerfRecordProfiler()
        p.configure({}, {})
        assert p._config == {}


class TestConformsToProtocol:
    def test_isinstance_check(self) -> None:
        assert isinstance(PerfRecordProfiler(), Profiler)


_PATCH_PROFILE = "autoforge.perf.profile.profile_pid"
_PATCH_ARCH = "autoforge.perf.arch.load_arch_profile"
_PATCH_SUMMARIZE = "autoforge.perf.analyze.summarize"


class TestProfileSuccess:
    @patch(_PATCH_SUMMARIZE)
    @patch(_PATCH_ARCH)
    @patch(_PATCH_PROFILE)
    def test_returns_success_with_summary(self, mock_profile, mock_arch, mock_summarize) -> None:
        mock_profile.return_value = MagicMock(
            success=True,
            counters={"cycles": 100.0},
            folded_stacks={"main;func": 5},
        )
        mock_arch.return_value = {"events": {}}
        mock_summarize.return_value = {"top_func": "func", "pct": 80}

        p = PerfRecordProfiler()
        p.configure({}, {"profiling": {"frequency": 99}})
        result = p.profile(pid=1234, duration=10, config={})

        assert result.success is True
        assert result.summary == {"top_func": "func", "pct": 80}
        mock_profile.assert_called_once()


class TestProfileFailure:
    @patch(_PATCH_PROFILE)
    def test_returns_failure_on_profile_error(self, mock_profile) -> None:
        mock_profile.return_value = MagicMock(success=False, error="perf not found")

        p = PerfRecordProfiler()
        p.configure({}, {})
        result = p.profile(pid=1234, duration=10, config={})

        assert result.success is False
        assert result.error == "perf not found"


class TestConfigMerge:
    @patch(_PATCH_SUMMARIZE, return_value={})
    @patch(_PATCH_ARCH, return_value={})
    @patch(_PATCH_PROFILE)
    def test_method_config_overrides_stored(self, mock_profile, mock_arch, mock_summarize) -> None:
        mock_profile.return_value = MagicMock(success=True, counters={}, folded_stacks={})

        p = PerfRecordProfiler()
        p.configure({}, {"profiling": {"frequency": 50, "sudo": True}})
        p.profile(pid=1, duration=5, config={"frequency": 200, "cpus": "4-8"})

        call_kwargs = mock_profile.call_args
        assert call_kwargs.kwargs["frequency"] == 200
        assert call_kwargs.kwargs["cpus"] == "4-8"
        assert call_kwargs.kwargs["sudo"] is True
