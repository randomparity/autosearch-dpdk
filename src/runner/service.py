"""Runner service main loop — polls for requests, builds DPDK, runs tests."""

from __future__ import annotations

import logging
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import tomllib

from src.protocol.schema import (
    STATUS_BUILDING,
    STATUS_CLAIMED,
    STATUS_COMPLETED,
    STATUS_RUNNING,
    TestRequest,
)
from src.runner.build import build_dpdk
from src.runner.execute import run_dts
from src.runner.protocol import (
    DEFAULT_REQUESTS_DIR,
    claim,
    fail,
    find_pending,
    update_status,
)
from src.runner.testpmd import run_testpmd

logger = logging.getLogger(__name__)


def load_config(path: str | None = None) -> dict:
    """Load runner configuration from a TOML file.

    Args:
        path: Path to the config file. If None, reads from
            AUTOSEARCH_CONFIG env var or defaults to 'config/runner.toml'.

    Returns:
        Parsed configuration dictionary.
    """
    import os

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
    )
    if result.returncode != 0:
        logger.error("git pull --rebase failed: %s", result.stderr.strip())
        return False
    return True


def process_request(request: TestRequest, request_path: Path, config: dict) -> None:
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

    update_status(request, STATUS_BUILDING, request_path)

    build_result = build_dpdk(
        source_path=source_path,
        commit=request.dpdk_commit,
        build_dir=build_dir,
        timeout=build_timeout,
        build_config=config.get("build", {}),
    )

    if not build_result.success:
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
        dts_result = run_dts(
            dts_path=dts_path,
            config=config,
            suites=request.test_suites,
            perf=request.perf,
            metric_path=request.metric_path,
            timeout=test_timeout,
        )
        if not dts_result.success:
            fail(request, request_path, error=dts_result.error or "DTS failed")
            return
        results_json = dts_result.results_json
        results_summary = dts_result.results_summary
        metric_value = dts_result.metric_value
    else:
        testpmd_result = run_testpmd(
            build_dir=build_dir,
            config=config,
            timeout=test_timeout,
        )
        if not testpmd_result.success:
            fail(
                request,
                request_path,
                error=testpmd_result.error or "testpmd failed",
            )
            return
        results_json = {"throughput_mpps": testpmd_result.throughput_mpps}
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


def main() -> None:
    """Runner service entry point."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )

    config = load_config()
    poll_interval = int(config.get("runner", {}).get("poll_interval", 30))
    requests_dir = DEFAULT_REQUESTS_DIR

    logger.info("Runner starting, poll_interval=%ds", poll_interval)

    recover_stale_requests(requests_dir)

    try:
        while True:
            if not _git_pull():
                logger.warning("Git pull failed, retrying next cycle")
                time.sleep(poll_interval)
                continue

            result = find_pending(requests_dir)
            if result is None:
                logger.debug("No pending requests, sleeping %ds", poll_interval)
                time.sleep(poll_interval)
                continue

            request, request_path = result

            logger.info("Found pending request %04d: %s", request.sequence, request.description)

            if not claim(request, request_path):
                logger.error("Failed to claim request %04d, skipping", request.sequence)
                continue

            process_request(request, request_path, config)

    except KeyboardInterrupt:
        logger.info("Runner stopped by user")


if __name__ == "__main__":
    main()
