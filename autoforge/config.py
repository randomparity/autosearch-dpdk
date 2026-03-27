"""Configuration loading with local overrides and variable resolution."""

from __future__ import annotations

import os
import re
import tomllib
from pathlib import Path
from typing import Any

from autoforge.pointer import REPO_ROOT

_VAR_RE = re.compile(r"\$\{([^}]+)\}")

_BUILTINS: dict[str, str] = {
    "REPO_ROOT": str(REPO_ROOT),
}


def _resolve_string(value: str) -> str:
    """Resolve ``${VAR}`` and ``${VAR:-default}`` references in a string.

    Built-in pseudo-variables (REPO_ROOT) are checked first, then
    ``os.environ``.  A missing variable with no default raises
    ``KeyError`` with an actionable message.
    """

    def _replace(match: re.Match[str]) -> str:
        expr = match.group(1)
        if ":-" in expr:
            name, default = expr.split(":-", 1)
            return _BUILTINS.get(name, os.environ.get(name, default))
        name = expr
        if name in _BUILTINS:
            return _BUILTINS[name]
        try:
            return os.environ[name]
        except KeyError:
            msg = (
                f"Environment variable ${{{name}}} is not set. "
                f"Set it in your shell or use ${{{{name}}:-default}} "
                f"for a fallback value."
            )
            raise KeyError(msg) from None

    return _VAR_RE.sub(_replace, value)


def resolve_vars(data: dict[str, Any]) -> dict[str, Any]:
    """Recursively resolve ``${VAR}`` references in all string values."""
    out: dict[str, Any] = {}
    for key, value in data.items():
        if isinstance(value, dict):
            out[key] = resolve_vars(value)
        elif isinstance(value, str) and "${" in value:
            out[key] = _resolve_string(value)
        else:
            out[key] = value
    return out


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Deep-merge *override* on top of *base*.

    - Nested dicts are merged recursively.
    - All other types (including lists) in *override* fully replace
      the *base* value.
    """
    merged: dict[str, Any] = {**base}
    for key, value in override.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_toml_with_local(base_path: Path) -> dict[str, Any]:
    """Load a TOML config file and merge a ``.local.toml`` sibling on top.

    Resolution order:
        1. Load *base_path* (e.g. ``runner.toml``).
        2. If a sibling ``<stem>.local.toml`` exists, deep-merge it over
           the base (local values win at every nesting level).
        3. Resolve ``${VAR}`` references in the merged result.

    Returns an empty dict if *base_path* does not exist and no local
    override is found either.
    """
    data: dict[str, Any] = {}

    if base_path.is_file():
        with open(base_path, "rb") as f:
            data = tomllib.load(f)

    local_path = base_path.with_suffix(".local.toml")
    if local_path.is_file():
        with open(local_path, "rb") as f:
            local_data = tomllib.load(f)
        data = deep_merge(data, local_data)

    return resolve_vars(data)
