"""Base class for phase-specific runners."""

from __future__ import annotations

import logging
import subprocess
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from autoforge.campaign import (
    CampaignConfig,
    project_name,
)
from autoforge.campaign import (
    project_config as _project_config,
)
from autoforge.plugins.loader import load_component
from autoforge.plugins.protocols import BuildResult, DeployResult
from autoforge.protocol import (
    GIT_TIMEOUT,
    STATUS_BUILDING,
    STATUS_BUILT,
    STATUS_CLAIMED,
    STATUS_DEPLOYED,
    STATUS_DEPLOYING,
    STATUS_PENDING,
    STATUS_RUNNING,
    StatusLiteral,
    TestRequest,
)
from autoforge.runner.protocol import (
    claim,
    complete_request,
    fail,
    find_by_status,
    update_status,
)

logger = logging.getLogger(__name__)


def git_pull() -> bool:
    """Pull latest changes with rebase. Returns True on success.

    Stashes uncommitted changes before pulling so that a co-located
    agent (or any other process modifying the working tree) does not
    block the rebase.
    """
    stash_result = subprocess.run(
        ["git", "stash", "--include-untracked"],
        capture_output=True,
        text=True,
        timeout=GIT_TIMEOUT,
    )
    stashed = stash_result.returncode == 0 and "No local changes" not in stash_result.stdout

    result = subprocess.run(
        ["git", "pull", "--rebase"],
        capture_output=True,
        text=True,
        timeout=GIT_TIMEOUT,
    )
    if result.returncode != 0:
        logger.error("git pull --rebase failed: %s", result.stderr.strip())

    if stashed:
        pop_result = subprocess.run(
            ["git", "stash", "pop"],
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT,
        )
        if pop_result.returncode != 0:
            logger.warning("git stash pop failed: %s", pop_result.stderr.strip())

    return result.returncode == 0


def recover_stale_requests(requests_dir: Path, stale_statuses: frozenset[str]) -> None:
    """Mark requests in stale statuses as failed on startup."""
    if not requests_dir.is_dir():
        return

    for path in sorted(requests_dir.glob("*.json")):
        try:
            request = TestRequest.read(path)
        except (ValueError, KeyError, TypeError, OSError) as exc:
            logger.warning("Skipping malformed request %s: %s", path.name, exc)
            continue

        if request.status in stale_statuses:
            logger.warning(
                "Recovering stale request %04d (status=%s)",
                request.sequence,
                request.status,
            )
            try:
                fail(request, path, error="runner restarted", failed_phase="claim")
            except RuntimeError:
                logger.error(
                    "Could not push failure status for request %04d; written locally",
                    request.sequence,
                )


def _run_build(
    request: TestRequest,
    request_path: Path,
    campaign: CampaignConfig,
    config: dict[str, Any],
) -> BuildResult | None:
    """Execute the build phase. Returns BuildResult on success, None on failure."""
    proj_cfg = _project_config(campaign)
    proj_name = project_name(campaign)
    paths = config.get("paths", {})
    timeouts = config.get("timeouts", {})
    source_path = Path(paths.get("source_dir", paths.get("dpdk_src", "/opt/dpdk")))
    build_dir = Path(paths.get("build_dir", "/tmp/build"))
    build_timeout = int(timeouts.get("build_minutes", 30)) * 60

    builder = load_component(
        proj_name,
        "build",
        request.build_plugin,
        project_config=proj_cfg,
        runner_config=config,
    )

    update_status(request, STATUS_BUILDING, request_path)
    build_result = builder.build(source_path, request.source_commit, build_dir, build_timeout)

    if not build_result.success:
        fail(
            request,
            request_path,
            error="Build failed",
            log_snippet=build_result.log,
            failed_phase="build",
        )
        return None

    update_status(request, STATUS_BUILT, request_path)
    return build_result


def _run_deploy(
    request: TestRequest,
    request_path: Path,
    campaign: CampaignConfig,
    config: dict[str, Any],
    build_result: BuildResult,
) -> DeployResult | None:
    """Execute the deploy phase. Returns DeployResult on success, None on failure."""
    proj_cfg = _project_config(campaign)
    proj_name = project_name(campaign)

    deployer = load_component(
        proj_name,
        "deploy",
        request.deploy_plugin,
        project_config=proj_cfg,
        runner_config=config,
    )

    update_status(request, STATUS_DEPLOYING, request_path)
    deploy_result = deployer.deploy(build_result)

    if not deploy_result.success:
        fail(
            request,
            request_path,
            error=deploy_result.error or "Deploy failed",
            deploy_log_snippet=deploy_result.log or None,
            failed_phase="deploy",
        )
        return None

    update_status(request, STATUS_DEPLOYED, request_path)
    return deploy_result


def _run_test(
    request: TestRequest,
    request_path: Path,
    campaign: CampaignConfig,
    config: dict[str, Any],
    deploy_result: DeployResult,
) -> None:
    """Execute the test phase and update request to completed/failed."""
    proj_cfg = _project_config(campaign)
    proj_name = project_name(campaign)
    timeouts = config.get("timeouts", {})
    test_timeout = int(timeouts.get("test_minutes", 10)) * 60

    tester = load_component(
        proj_name,
        "test",
        request.test_plugin,
        project_config=proj_cfg,
        runner_config=config,
    )

    update_status(request, STATUS_RUNNING, request_path)
    test_result = tester.test(deploy_result, timeout=test_timeout)

    if not test_result.success:
        fail(
            request,
            request_path,
            error=test_result.error or "Test failed",
            test_log_snippet=test_result.log or None,
            failed_phase="test",
        )
        return

    complete_request(
        request,
        request_path,
        results_json=test_result.results_json,
        results_summary=test_result.results_summary,
        metric_value=test_result.metric_value,
    )


class PhaseRunner(ABC):
    """Base class for runners that handle specific pipeline phases."""

    watch_status: StatusLiteral
    stale_statuses: frozenset[str]

    def __init__(
        self,
        config: dict[str, Any],
        campaign: CampaignConfig,
        requests_dir: Path,
    ) -> None:
        self.config = config
        self.campaign = campaign
        self.requests_dir = requests_dir
        self.runner_id = config.get("runner", {}).get("runner_id", "")
        self.poll_interval = int(config.get("runner", {}).get("poll_interval", 30))

    @abstractmethod
    def execute_phase(self, request: TestRequest, request_path: Path) -> None:
        """Execute this runner's phase on a request."""

    def poll_loop(self) -> None:
        """Main loop: pull, find requests, claim, execute."""
        recover_stale_requests(self.requests_dir, self.stale_statuses)

        logger.info(
            "Runner starting: phase=%s, poll=%ds, dir=%s",
            type(self).__name__,
            self.poll_interval,
            self.requests_dir,
        )

        try:
            while True:
                if not git_pull():
                    logger.warning("Git pull failed, retrying next cycle")
                    time.sleep(self.poll_interval)
                    continue

                result = find_by_status(self.requests_dir, self.watch_status)
                if result is None:
                    logger.debug(
                        "No %s requests, sleeping %ds",
                        self.watch_status,
                        self.poll_interval,
                    )
                    time.sleep(self.poll_interval)
                    continue

                request, request_path = result
                logger.info(
                    "Found %s request %04d: %s",
                    self.watch_status,
                    request.sequence,
                    request.description,
                )

                if self.watch_status == STATUS_PENDING and not claim(request, request_path):
                    logger.error(
                        "Failed to claim request %04d, skipping",
                        request.sequence,
                    )
                    continue

                try:
                    self.execute_phase(request, request_path)
                except Exception:
                    logger.exception(
                        "Unhandled error in execute_phase for request %04d",
                        request.sequence,
                    )
                    try:
                        fail(request, request_path, error="runner: unhandled exception")
                    except RuntimeError:
                        logger.error(
                            "Could not push failure status for request %04d; written locally",
                            request.sequence,
                        )

        except KeyboardInterrupt:
            logger.info("Runner stopped by user")


class BuildRunner(PhaseRunner):
    """Watches for pending requests, builds, transitions to built."""

    watch_status: StatusLiteral = STATUS_PENDING
    stale_statuses = frozenset({STATUS_CLAIMED, STATUS_BUILDING, STATUS_BUILT})

    def execute_phase(self, request: TestRequest, request_path: Path) -> None:
        _run_build(request, request_path, self.campaign, self.config)


class DeployRunner(PhaseRunner):
    """Watches for built requests, deploys, transitions to deployed."""

    watch_status: StatusLiteral = STATUS_BUILT
    stale_statuses = frozenset({STATUS_DEPLOYING, STATUS_DEPLOYED})

    def execute_phase(self, request: TestRequest, request_path: Path) -> None:
        build_result = BuildResult(
            success=True,
            log="",
            duration_seconds=0,
            artifacts={
                "build_dir": self.config.get("paths", {}).get("build_dir", "/tmp/build"),
            },
        )
        _run_deploy(request, request_path, self.campaign, self.config, build_result)


class TestRunner(PhaseRunner):
    """Watches for deployed requests, tests, transitions to completed."""

    watch_status: StatusLiteral = STATUS_DEPLOYED
    stale_statuses = frozenset({STATUS_RUNNING})

    def execute_phase(self, request: TestRequest, request_path: Path) -> None:
        deploy_result = DeployResult(
            success=True,
            target_info={
                "build_dir": self.config.get("paths", {}).get("build_dir", "/tmp/build"),
            },
        )
        _run_test(request, request_path, self.campaign, self.config, deploy_result)


class FullRunner(PhaseRunner):
    """Runs all phases sequentially (single-machine mode)."""

    watch_status: StatusLiteral = STATUS_PENDING
    stale_statuses = frozenset(
        {
            STATUS_CLAIMED,
            STATUS_BUILDING,
            STATUS_BUILT,
            STATUS_DEPLOYING,
            STATUS_DEPLOYED,
            STATUS_RUNNING,
        }
    )

    def execute_phase(self, request: TestRequest, request_path: Path) -> None:
        build_result = _run_build(request, request_path, self.campaign, self.config)
        if build_result is None:
            return

        deploy_result = _run_deploy(request, request_path, self.campaign, self.config, build_result)
        if deploy_result is None:
            return

        _run_test(request, request_path, self.campaign, self.config, deploy_result)
