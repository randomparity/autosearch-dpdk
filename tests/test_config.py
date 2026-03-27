"""Tests for autoforge.config — variable resolution, deep merge, local overrides."""

from __future__ import annotations

import textwrap
from pathlib import Path
from typing import Any

import pytest

from autoforge.config import (
    _resolve_string,
    deep_merge,
    load_toml_with_local,
    resolve_vars,
)


class TestResolveString:
    """Unit tests for _resolve_string."""

    def test_no_vars(self) -> None:
        assert _resolve_string("plain text") == "plain text"

    def test_env_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TEST_VAR", "hello")
        assert _resolve_string("${TEST_VAR}") == "hello"

    def test_env_var_with_surrounding_text(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DIR", "/opt")
        assert _resolve_string("${DIR}/subdir/file") == "/opt/subdir/file"

    def test_multiple_vars(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("A", "one")
        monkeypatch.setenv("B", "two")
        assert _resolve_string("${A}-${B}") == "one-two"

    def test_missing_var_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("NONEXISTENT_VAR_XYZ", raising=False)
        with pytest.raises(KeyError, match="NONEXISTENT_VAR_XYZ"):
            _resolve_string("${NONEXISTENT_VAR_XYZ}")

    def test_default_value(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MISSING", raising=False)
        assert _resolve_string("${MISSING:-fallback}") == "fallback"

    def test_default_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MISSING", raising=False)
        assert _resolve_string("${MISSING:-}") == ""

    def test_default_not_used_when_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PRESENT", "actual")
        assert _resolve_string("${PRESENT:-fallback}") == "actual"

    def test_repo_root_builtin(self) -> None:
        result = _resolve_string("${REPO_ROOT}/projects/vllm")
        assert result.endswith("/projects/vllm")
        assert "${" not in result

    def test_repo_root_takes_precedence_over_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("REPO_ROOT", "/should/not/use/this")
        result = _resolve_string("${REPO_ROOT}")
        assert result != "/should/not/use/this"


class TestResolveVars:
    """Unit tests for resolve_vars."""

    def test_nested_dicts(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SECRET", "s3cr3t")
        data: dict[str, Any] = {
            "outer": {"inner": "${SECRET}"},
            "plain": "no vars here",
        }
        result = resolve_vars(data)
        assert result["outer"]["inner"] == "s3cr3t"
        assert result["plain"] == "no vars here"

    def test_non_string_passthrough(self) -> None:
        data: dict[str, Any] = {"count": 42, "enabled": True, "items": [1, 2]}
        result = resolve_vars(data)
        assert result == data

    def test_mixed_types(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PORT", "8080")
        data: dict[str, Any] = {"port_str": "${PORT}", "port_int": 8080}
        result = resolve_vars(data)
        assert result["port_str"] == "8080"
        assert result["port_int"] == 8080


class TestDeepMerge:
    """Unit tests for deep_merge."""

    def test_non_overlapping_keys(self) -> None:
        base = {"a": 1}
        override = {"b": 2}
        assert deep_merge(base, override) == {"a": 1, "b": 2}

    def test_override_scalar(self) -> None:
        base = {"a": 1}
        override = {"a": 2}
        assert deep_merge(base, override) == {"a": 2}

    def test_nested_merge(self) -> None:
        base = {"section": {"key1": "a", "key2": "b"}}
        override = {"section": {"key2": "B", "key3": "c"}}
        result = deep_merge(base, override)
        assert result == {"section": {"key1": "a", "key2": "B", "key3": "c"}}

    def test_list_replaces(self) -> None:
        base = {"args": ["--flag1", "--flag2"]}
        override = {"args": ["--flag3"]}
        assert deep_merge(base, override) == {"args": ["--flag3"]}

    def test_override_dict_with_scalar(self) -> None:
        base: dict[str, Any] = {"x": {"nested": True}}
        override: dict[str, Any] = {"x": "flat"}
        assert deep_merge(base, override) == {"x": "flat"}

    def test_does_not_mutate_inputs(self) -> None:
        base = {"section": {"key": "original"}}
        override = {"section": {"key": "changed"}}
        deep_merge(base, override)
        assert base["section"]["key"] == "original"

    def test_deeply_nested(self) -> None:
        base = {"a": {"b": {"c": 1, "d": 2}}}
        override = {"a": {"b": {"c": 99}}}
        assert deep_merge(base, override) == {"a": {"b": {"c": 99, "d": 2}}}


class TestLoadTomlWithLocal:
    """Integration tests for load_toml_with_local."""

    def test_base_only(self, tmp_path: Path) -> None:
        base = tmp_path / "runner.toml"
        base.write_text('[runner]\nphase = "all"\n')
        result = load_toml_with_local(base)
        assert result == {"runner": {"phase": "all"}}

    def test_local_overrides_base(self, tmp_path: Path) -> None:
        base = tmp_path / "runner.toml"
        base.write_text('[paths]\nsource = "/default"\nbuild = "/tmp"\n')
        local = tmp_path / "runner.local.toml"
        local.write_text('[paths]\nbuild = "/fast-ssd"\n')
        result = load_toml_with_local(base)
        assert result["paths"]["source"] == "/default"
        assert result["paths"]["build"] == "/fast-ssd"

    def test_local_only(self, tmp_path: Path) -> None:
        base = tmp_path / "runner.toml"
        local = tmp_path / "runner.local.toml"
        local.write_text('[paths]\nbuild = "/data"\n')
        result = load_toml_with_local(base)
        assert result == {"paths": {"build": "/data"}}

    def test_neither_exists(self, tmp_path: Path) -> None:
        base = tmp_path / "runner.toml"
        result = load_toml_with_local(base)
        assert result == {}

    def test_var_resolution_in_merged(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MY_TOKEN", "abc123")
        base = tmp_path / "deploy.toml"
        base.write_text(
            textwrap.dedent("""\
                [deploy]
                model = "Qwen/Qwen3-0.6B"

                [deploy.env]
                TOKEN = "${MY_TOKEN}"
            """)
        )
        result = load_toml_with_local(base)
        assert result["deploy"]["env"]["TOKEN"] == "abc123"
        assert result["deploy"]["model"] == "Qwen/Qwen3-0.6B"

    def test_repo_root_in_paths(self, tmp_path: Path) -> None:
        base = tmp_path / "runner.toml"
        base.write_text('[paths]\nsource_dir = "${REPO_ROOT}/projects/vllm/repo"\n')
        result = load_toml_with_local(base)
        assert result["paths"]["source_dir"].endswith("/projects/vllm/repo")
        assert "${" not in result["paths"]["source_dir"]
