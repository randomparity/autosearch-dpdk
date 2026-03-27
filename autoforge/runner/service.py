"""Runner service entry point — dispatches to phase-specific runners."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from autoforge.campaign import load_campaign, resolve_campaign_path
from autoforge.config import load_toml_with_local
from autoforge.logging_config import setup_logging
from autoforge.pointer import REPO_ROOT, load_pointer
from autoforge.runner.base import (
    BuildRunner,
    DeployRunner,
    FullRunner,
    TestRunner,
)

logger = logging.getLogger(__name__)

PHASE_RUNNERS = {
    "all": FullRunner,
    "build": BuildRunner,
    "deploy": DeployRunner,
    "test": TestRunner,
}


def resolve_config_path(explicit: str | None = None) -> str:
    """Resolve runner config path: explicit > env var > pointer-based default."""
    if explicit:
        return explicit
    env = os.environ.get("AUTOFORGE_CONFIG")
    if env:
        return env
    pointer = load_pointer()
    return str(REPO_ROOT / "projects" / pointer["project"] / "runner.toml")


def load_config(path: str | None = None) -> dict[str, Any]:
    """Load runner configuration from a TOML file.

    The base ``.toml`` file contains shared defaults (tracked in git).
    A sibling ``.local.toml`` file is deep-merged on top for
    system-specific overrides (gitignored).  ``${VAR}`` references
    are resolved after merging.

    Raises:
        FileNotFoundError: If neither the base nor local config exists.
    """
    config_path = Path(resolve_config_path(path))
    if not config_path.is_file() and not config_path.with_suffix(".local.toml").is_file():
        msg = (
            f"Runner config not found: {config_path}\n"
            "Create a runner.local.toml with system-specific overrides."
        )
        raise FileNotFoundError(msg)
    return load_toml_with_local(config_path)


def main() -> None:
    """Runner service entry point."""
    config = load_config()
    runner_cfg = config.get("runner", {})

    setup_logging(
        level_name=runner_cfg.get("log_level"),
        log_file=runner_cfg.get("log_file"),
    )

    campaign_path = resolve_campaign_path()
    campaign = load_campaign(campaign_path)
    pointer = load_pointer()
    req_dir = (
        REPO_ROOT / "projects" / pointer["project"] / "sprints" / pointer["sprint"] / "requests"
    )

    phase = runner_cfg.get("phase", "all")
    runner_cls = PHASE_RUNNERS.get(phase)
    if runner_cls is None:
        msg = f"Unknown runner phase {phase!r}, must be one of {sorted(PHASE_RUNNERS)}"
        raise ValueError(msg)

    runner = runner_cls(config=config, campaign=campaign, requests_dir=req_dir)
    logger.info("Starting %s runner (id=%s)", phase, runner.runner_id or "unset")
    runner.poll_loop()


if __name__ == "__main__":
    main()
