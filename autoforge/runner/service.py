"""Runner service main loop — polls for requests, loads plugins, runs tests."""

from __future__ import annotations

import logging
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import tomllib

from autoforge.logging_config import setup_logging
from autoforge.plugins import load_plugin
from autoforge.plugins.protocols import Plugin
from autoforge.protocol import (
    STATUS_BUILDING,
    STATUS_CLAIMED,
    STATUS_COMPLETED,
    STATUS_RUNNING,
    TestRequest,
)
from autoforge.runner.protocol import (
    claim,
    fail,
    find_pending,
    update_status,
)

logger = logging.getLogger(__name__)


def load_config(path: str | None = None) -> dict:
    """Load runner configuration from a TOML file.

    Args:
        path: Path to the config file. If None, reads from
            AUTOFORGE_CONFIG env var or defaults to 'config/runner.toml'.

    Returns:
        Parsed configuration dictionary.
    """
    config_path = path or os.environ.get("AUTOFORGE_CONFIG", "config/runner.toml")
    with open(config_path, "rb") as f:
        return tomllib.load(f)


def recover_stale_requests(requests_dir: Path) -> None:
    """Mark any claimed or building requests as failed on startup.

    This handles the case where a previous runner instance crashed
    mid-processing.

    Args:
        requests_dir: Directory containing request JSON files.
    """
    if not requests_dir.is_dir():
        return

    stale_statuses = {STATUS_CLAIMED, STATUS_BUILDING, STATUS_RUNNING}

    for path in sorted(requests_dir.glob("*.json")):
        try:
            request = TestRequest.read(path)
        except (ValueError, KeyError, TypeError) as exc:
            logger.warning("Skipping malformed request %s: %s", path.name, exc)
            continue

        if request.status in stale_statuses:
            logger.warning(
                "Recovering stale request %04d (status=%s)", request.sequence, request.status
            )
            fail(request, path, error="runner restarted")


def _git_pull() -> bool:
    """Pull latest changes with rebase. Returns True on success."""
    result = subprocess.run(
        ["git", "pull", "--rebase"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        logger.error("git pull --rebase failed: %s", result.stderr.strip())
        return False
    return True


def execute_request(
    request: TestRequest,
    request_path: Path,
    config: dict,
    plugin: Plugin,
    project_config: dict,
) -> None:
    """Process a single test request through build, deploy, and test phases.

    Args:
        request: The claimed test request.
        request_path: Path to the request JSON file.
        config: Runner configuration dictionary.
        plugin: Loaded plugin instance.
        project_config: Project-specific config from campaign TOML.
    """
    paths = config.get("paths", {})
    timeouts = config.get("timeouts", {})
    source_path = Path(paths.get("dpdk_src", "/opt/dpdk"))
    build_dir = Path(paths.get("build_dir", "/tmp/dpdk-build"))
    build_timeout = int(timeouts.get("build_minutes", 30)) * 60
    test_timeout = int(timeouts.get("test_minutes", 10)) * 60

    logger.info(
        "Processing request %04d: commit=%s plugin=%s",
        request.sequence,
        request.source_commit[:12],
        plugin.name,
    )

    # Build phase
    update_status(request, STATUS_BUILDING, request_path)
    builder = plugin.create_builder()
    builder.configure(project_config, config)
    build_result = builder.build(source_path, request.source_commit, build_dir, build_timeout)

    logger.info(
        "Build %s in %.1fs",
        "succeeded" if build_result.success else "FAILED",
        build_result.duration_seconds,
    )

    if not build_result.success:
        logger.error("Build failed, last output:\n%s", build_result.log[-500:])
        fail(
            request,
            request_path,
            error="Build failed",
            log_snippet=build_result.log,
        )
        return

    # Deploy phase
    update_status(request, STATUS_RUNNING, request_path)
    deployer = plugin.create_deployer()
    deployer.configure(project_config, config)
    deploy_result = deployer.deploy(build_result)

    if not deploy_result.success:
        logger.error("Deploy failed: %s", deploy_result.error)
        fail(request, request_path, error=deploy_result.error or "Deploy failed")
        return

    # Test phase
    tester = plugin.create_tester()
    tester.configure(project_config, config)
    test_result = tester.test(deploy_result, timeout=test_timeout)

    if not test_result.success:
        logger.error("Test failed: %s", test_result.error)
        fail(request, request_path, error=test_result.error or "Test failed")
        return

    logger.info(
        "Test completed in %.1fs, metric=%.4f",
        test_result.duration_seconds,
        test_result.metric_value or 0,
    )

    pushed = update_status(
        request,
        STATUS_COMPLETED,
        request_path,
        results_json=test_result.results_json,
        results_summary=test_result.results_summary,
        metric_value=test_result.metric_value,
        completed_at=datetime.now(timezone.utc).isoformat(),
    )
    if not pushed:
        logger.error(
            "Results for request %04d written locally but not pushed",
            request.sequence,
        )
    else:
        logger.info("Request %04d completed successfully", request.sequence)


def _load_campaign() -> dict:
    """Load campaign.toml from the repo root."""
    repo_root = Path(__file__).resolve().parent.parent.parent
    campaign_path = repo_root / "config" / "campaign.toml"
    if not campaign_path.exists():
        msg = f"Campaign config not found: {campaign_path}"
        raise FileNotFoundError(msg)
    with open(campaign_path, "rb") as f:
        return tomllib.load(f)


def _load_requests_dir(campaign: dict) -> Path:
    """Derive the requests directory from campaign sprint config."""
    repo_root = Path(__file__).resolve().parent.parent.parent
    sprint_name = campaign.get("sprint", {}).get("name")
    if not sprint_name:
        msg = "No [sprint] name in campaign.toml. Run 'autoforge sprint init' first."
        raise ValueError(msg)
    return repo_root / "sprints" / sprint_name / "requests"


def main() -> None:
    """Runner service entry point."""
    config = load_config()
    runner_cfg = config.get("runner", {})

    setup_logging(
        level_name=runner_cfg.get("log_level"),
        log_file=runner_cfg.get("log_file"),
    )

    campaign = _load_campaign()
    project_config = campaign.get("project", {})
    plugin_name = project_config.get("plugin", "dpdk")
    plugin = load_plugin(plugin_name)
    logger.info("Loaded plugin: %s", plugin.name)

    poll_interval = int(runner_cfg.get("poll_interval", 30))
    req_dir = _load_requests_dir(campaign)

    logger.info("Runner starting, poll_interval=%ds, requests_dir=%s", poll_interval, req_dir)

    recover_stale_requests(req_dir)

    try:
        while True:
            if not _git_pull():
                logger.warning("Git pull failed, retrying next cycle")
                time.sleep(poll_interval)
                continue

            result = find_pending(req_dir)
            if result is None:
                logger.debug("No pending requests, sleeping %ds", poll_interval)
                time.sleep(poll_interval)
                continue

            request, request_path = result

            logger.info("Found pending request %04d: %s", request.sequence, request.description)

            logger.info("Claiming request %04d", request.sequence)
            if not claim(request, request_path):
                logger.error("Failed to claim request %04d, skipping", request.sequence)
                continue

            logger.info("Claimed request %04d, starting processing", request.sequence)
            execute_request(request, request_path, config, plugin, project_config)

    except KeyboardInterrupt:
        logger.info("Runner stopped by user")


if __name__ == "__main__":
    main()
