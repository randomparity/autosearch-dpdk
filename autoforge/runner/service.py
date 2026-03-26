"""Runner service entry point — dispatches to phase-specific runners."""

from __future__ import annotations

import logging
import os
import tomllib

from autoforge.campaign import REPO_ROOT, load_campaign, load_pointer, resolve_campaign_path
from autoforge.logging_config import setup_logging
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


def load_config(path: str | None = None) -> dict:
    """Load runner configuration from a TOML file.

    Raises:
        FileNotFoundError: If the config file doesn't exist (with guidance).
        tomllib.TOMLDecodeError: If the file is not valid TOML.
    """
    config_path = resolve_config_path(path)
    try:
        with open(config_path, "rb") as f:
            return tomllib.load(f)
    except FileNotFoundError:
        msg = (
            f"Runner config not found: {config_path}\n"
            "Copy from runner.toml.example and edit for your environment."
        )
        raise FileNotFoundError(msg) from None


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
