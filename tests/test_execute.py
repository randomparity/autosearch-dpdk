"""Tests for DTS execution helpers."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

PLUGIN_PATH = Path(__file__).parent.parent / "projects" / "dpdk" / "tests" / "dts-mlx5.py"
MODULE_NAME = "autoforge_plugin_dts_mlx5"


def _load_dts_module():
    spec = importlib.util.spec_from_file_location(MODULE_NAME, PLUGIN_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[MODULE_NAME] = mod
    spec.loader.exec_module(mod)
    return mod


_mod = _load_dts_module()
_read_json_file = _mod._read_json_file
_read_text_file = _mod._read_text_file


class TestReadJsonFile:
    def test_reads_valid_json(self, tmp_path) -> None:
        path = tmp_path / "data.json"
        path.write_text(json.dumps({"key": "value"}))
        result = _read_json_file(path)
        assert result == {"key": "value"}

    def test_missing_file_returns_none(self, tmp_path) -> None:
        path = tmp_path / "missing.json"
        assert _read_json_file(path) is None

    def test_invalid_json_returns_none(self, tmp_path) -> None:
        path = tmp_path / "bad.json"
        path.write_text("not json")
        assert _read_json_file(path) is None


class TestReadTextFile:
    def test_reads_text(self, tmp_path) -> None:
        path = tmp_path / "data.txt"
        path.write_text("hello world")
        assert _read_text_file(path) == "hello world"

    def test_missing_file_returns_none(self, tmp_path) -> None:
        path = tmp_path / "missing.txt"
        assert _read_text_file(path) is None
