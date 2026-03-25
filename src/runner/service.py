"""Runner service main loop — polls for requests, builds DPDK, runs tests."""

from __future__ import annotations

import logging
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import tomllib

from src.logging_config import setup_logging
from src.protocol import (
    STATUS_BUILDING,
    STATUS_CLAIMED,
    STATUS_COMPLETED,
    STATUS_RUNNING,
    TestRequest,
)
from src.runner.build import build_dpdk
from src.runner.execute import run_dts
from src.runner.protocol import (
    claim,
    fail,
    find_pending,
    update_status,
)
from src.runner.testpmd import run_testpmd_repeated

logger = logging.getLogger(__name__)


def load_config(path: str | None = None) -> dict:
    """Load runner configuration from a TOML file.

    Args:
        path: Path to the config file. If None, reads from
            AUTOSEARCH_CONFIG env var or defaults to 'config/runner.toml'.

    Returns:
        Parsed configuration dictionary.
    """
    config_path = path or os.environ.get("AUTOSEARCH_CONFIG", "config/runner.toml")
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


def execute_request(request: TestRequest, request_path: Path, config: dict) -> None:
    """Process a single test request through build and test phases.

    Args:
        request: The claimed test request.
        request_path: Path to the request JSON file.
        config: Runner configuration dictionary.
    """
    paths = config.get("paths", {})
    timeouts = config.get("timeouts", {})
    source_path = Path(paths.get("dpdk_src", "/opt/dpdk"))
    build_dir = Path(paths.get("build_dir", "/tmp/dpdk-build"))
    build_timeout = int(timeouts.get("build_minutes", 30)) * 60
    test_timeout = int(timeouts.get("test_minutes", 10)) * 60

    logger.info(
        "Processing request %04d: commit=%s backend=%s",
        request.sequence,
        request.dpdk_commit[:12],
        getattr(request, "backend", "testpmd"),
    )

    update_status(request, STATUS_BUILDING, request_path)
    logger.info("Building DPDK at %s in %s", request.dpdk_commit[:12], build_dir)

    build_result = build_dpdk(
        source_path=source_path,
        commit=request.dpdk_commit,
        build_dir=build_dir,
        timeout=build_timeout,
        build_config=config.get("build", {}),
    )

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
            error="DPDK build failed",
            log_snippet=build_result.log,
        )
        return

    update_status(request, STATUS_RUNNING, request_path)

    backend = getattr(request, "backend", "testpmd")

    if backend == "dts":
        dts_path = Path(paths.get("dts_dir", "/opt/dts"))
        logger.info("Running DTS at %s", dts_path)
        dts_result = run_dts(
            dts_path=dts_path,
            suites=request.test_suites,
            perf=request.perf,
            metric_path=request.metric_path,
            timeout=test_timeout,
        )
        if not dts_result.success:
            logger.error("DTS failed: %s", dts_result.error)
            fail(request, request_path, error=dts_result.error or "DTS failed")
            return
        logger.info("DTS completed in %.1fs", dts_result.duration_seconds)
        results_json = dts_result.results_json
        results_summary = dts_result.results_summary
        metric_value = dts_result.metric_value
    else:
        logger.info("Running testpmd measurement")
        profile_config = config.get("profiling", {})
        testpmd_result = run_testpmd_repeated(
            build_dir=build_dir,
            config=config,
            timeout=test_timeout,
            profile_config=profile_config,
        )
        if not testpmd_result.success:
            logger.error("testpmd failed: %s", testpmd_result.error)
            fail(
                request,
                request_path,
                error=testpmd_result.error or "testpmd failed",
            )
            return
        logger.info(
            "testpmd completed in %.1fs, throughput=%.4f Mpps",
            testpmd_result.duration_seconds,
            testpmd_result.throughput_mpps or 0,
        )
        results_json = {"throughput_mpps": testpmd_result.throughput_mpps}
        if testpmd_result.profile_summary:
            results_json["profiling"] = testpmd_result.profile_summary
        results_summary = testpmd_result.port_stats
        metric_value = testpmd_result.throughput_mpps

    pushed = update_status(
        request,
        STATUS_COMPLETED,
        request_path,
        results_json=results_json,
        results_summary=results_summary,
        metric_value=metric_value,
        completed_at=datetime.now(timezone.utc).isoformat(),
    )
    if not pushed:
        logger.error(
            "Results for request %04d written locally but not pushed",
            request.sequence,
        )
    else:
        logger.info("Request %04d completed successfully", request.sequence)


def _load_requests_dir() -> Path:
    """Derive the requests directory from campaign.toml sprint config."""
    repo_root = Path(__file__).resolve().parent.parent.parent
    campaign_path = repo_root / "config" / "campaign.toml"
    if not campaign_path.exists():
        msg = f"Campaign config not found: {campaign_path}"
        raise FileNotFoundError(msg)
    with open(campaign_path, "rb") as f:
        campaign = tomllib.load(f)
    sprint_name = campaign.get("sprint", {}).get("name")
    if not sprint_name:
        msg = "No [sprint] name in campaign.toml. Run 'autosearch sprint init' first."
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

    poll_interval = int(runner_cfg.get("poll_interval", 30))
    req_dir = _load_requests_dir()

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
            execute_request(request, request_path, config)

    except KeyboardInterrupt:
        logger.info("Runner stopped by user")


if __name__ == "__main__":
    main()
