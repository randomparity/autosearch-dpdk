"""Tests for DPDK local-server build plugin."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

from autoforge.plugins.protocols import Builder, BuildResult

PLUGIN_PATH = Path(__file__).parent.parent / "projects" / "dpdk" / "builds" / "local-server.py"
MODULE_NAME = "autoforge_plugin_local_server_test"


def _load_module():
    spec = importlib.util.spec_from_file_location(MODULE_NAME, PLUGIN_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[MODULE_NAME] = mod
    spec.loader.exec_module(mod)
    return mod


_mod = _load_module()
LocalServerBuilder = _mod.LocalServerBuilder


def _make_builder(build_config: dict | None = None) -> _mod.LocalServerBuilder:
    b = LocalServerBuilder()
    b.configure({}, {"build": build_config or {}})
    return b


def _ok(stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], 0, stdout=stdout, stderr=stderr)


def _fail(stderr: str = "error") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], 1, stdout="", stderr=stderr)


class TestConfigure:
    def test_stores_build_config(self) -> None:
        b = LocalServerBuilder()
        b.configure({}, {"build": {"jobs": 4, "cross_file": "/tmp/cross.txt"}})
        assert b._build_config == {"jobs": 4, "cross_file": "/tmp/cross.txt"}

    def test_missing_build_section_defaults_empty(self) -> None:
        b = LocalServerBuilder()
        b.configure({}, {})
        assert b._build_config == {}

    def test_conforms_to_builder_protocol(self) -> None:
        assert isinstance(LocalServerBuilder(), Builder)


class TestBuildSuccess:
    @patch("subprocess.run")
    def test_fresh_build_runs_meson_setup(self, mock_run, tmp_path) -> None:
        mock_run.return_value = _ok()
        src = tmp_path / "src"
        src.mkdir()
        build_dir = tmp_path / "build"
        b = _make_builder()
        result = b.build(src, "abc123", build_dir, 300)

        assert result.success is True
        assert str(build_dir) in result.artifacts.get("build_dir", "")

        calls = mock_run.call_args_list
        assert len(calls) == 3
        meson_args = calls[1][0][0]
        assert "meson" in meson_args[0]
        assert "--reconfigure" not in meson_args

    @patch("subprocess.run")
    def test_reconfigure_when_build_dat_exists(self, mock_run, tmp_path) -> None:
        mock_run.return_value = _ok()
        src = tmp_path / "src"
        src.mkdir()
        build_dir = tmp_path / "build"
        (build_dir / "meson-private").mkdir(parents=True)
        (build_dir / "meson-private" / "build.dat").touch()

        b = _make_builder()
        result = b.build(src, "abc123", build_dir, 300)

        assert result.success is True
        meson_args = mock_run.call_args_list[1][0][0]
        assert "--reconfigure" in meson_args

    @patch("subprocess.run")
    def test_cross_file_in_meson_args(self, mock_run, tmp_path) -> None:
        mock_run.return_value = _ok()
        b = _make_builder({"cross_file": "/opt/cross.txt"})
        b.build(tmp_path / "src", "abc123", tmp_path / "build", 300)

        meson_args = mock_run.call_args_list[1][0][0]
        assert "--cross-file" in meson_args
        assert "/opt/cross.txt" in meson_args


class TestBuildFailure:
    @patch("subprocess.run")
    def test_checkout_nonzero(self, mock_run, tmp_path) -> None:
        mock_run.return_value = _fail("checkout error")
        b = _make_builder()
        result = b.build(tmp_path / "src", "abc123", tmp_path / "build", 300)
        assert isinstance(result, BuildResult)
        assert result.success is False
        assert "checkout error" in result.log

    @patch("subprocess.run")
    def test_meson_nonzero(self, mock_run, tmp_path) -> None:
        mock_run.side_effect = [_ok(), _fail("meson error")]
        b = _make_builder()
        result = b.build(tmp_path / "src", "abc123", tmp_path / "build", 300)
        assert result.success is False
        assert "meson error" in result.log

    @patch("subprocess.run")
    def test_ninja_nonzero(self, mock_run, tmp_path) -> None:
        mock_run.side_effect = [_ok(), _ok(), _fail("ninja error")]
        b = _make_builder()
        result = b.build(tmp_path / "src", "abc123", tmp_path / "build", 300)
        assert result.success is False
        assert "ninja error" in result.log

    @patch("subprocess.run")
    def test_timeout_expired(self, mock_run, tmp_path) -> None:
        mock_run.side_effect = subprocess.TimeoutExpired("cmd", 300)
        b = _make_builder()
        result = b.build(tmp_path / "src", "abc123", tmp_path / "build", 300)
        assert result.success is False
        assert "TIMEOUT" in result.log
