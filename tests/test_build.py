"""Tests for DPDK build orchestration."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

PLUGIN_PATH = Path(__file__).parent.parent / "projects" / "dpdk" / "builds" / "local-server.py"
MODULE_NAME = "autoforge_plugin_local_server"


def _load_builder_module():
    spec = importlib.util.spec_from_file_location(MODULE_NAME, PLUGIN_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[MODULE_NAME] = mod
    spec.loader.exec_module(mod)
    return mod


_mod = _load_builder_module()
_truncate_log = _mod._truncate_log


class TestTruncateLog:
    def test_short_log_unchanged(self) -> None:
        log = "line1\nline2\nline3"
        assert _truncate_log(log, max_lines=10) == log

    def test_long_log_keeps_last_n(self) -> None:
        lines = [f"line{i}" for i in range(20)]
        log = "\n".join(lines)
        result = _truncate_log(log, max_lines=5)
        assert result == "\n".join(lines[-5:])

    def test_exact_boundary(self) -> None:
        lines = [f"line{i}" for i in range(5)]
        log = "\n".join(lines)
        assert _truncate_log(log, max_lines=5) == log

    def test_empty_log(self) -> None:
        assert _truncate_log("", max_lines=10) == ""
