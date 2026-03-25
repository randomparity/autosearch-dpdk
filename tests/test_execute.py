"""Tests for DTS execution module."""

from __future__ import annotations

import json

from autoforge_dpdk.tester import _read_json_file, _read_text_file


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
