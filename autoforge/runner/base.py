"""Base class for phase-specific runners."""

from __future__ import annotations

import logging
import subprocess
import time
from abc import ABC, abstractmethod
from pathlib import Path

from autoforge.protocol import (
    STATUS_BUILDING,
    STATUS_BUILT,
    STATUS_CLAIMED,
    STATUS_DEPLOYED,
    STATUS_DEPLOYING,
    STATUS_RUNNING,
    StatusLiteral,
    TestRequest,
)
from autoforge.runner.protocol import (
    claim,
    fail,
    find_by_status,
    update_status,
)

logger = logging.getLogger(__name__)


GIT_TIMEOUT = 60


def git_pull() -> bool:
    """Pull latest changes with rebase. Returns True on success."""
    result = subprocess.run(
        ["git", "pull", "--rebase"],
        capture_output=True,
        text=True,
        timeout=GIT_TIMEOUT,
    )
    if result.returncode != 0:
        logger.error("git pull --rebase failed: %s", result.stderr.strip())
        return False
    return True


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
            fail(request, path, error="runner restarted")


class PhaseRunner(ABC):
    """Base class for runners that handle specific pipeline phases."""

    watch_status: StatusLiteral
    stale_statuses: frozenset[str]

    def __init__(
        self,
        config: dict,
        campaign: dict,
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

                if self.watch_status == "pending" and not claim(request, request_path):
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
                    fail(request, request_path, error="runner: unhandled exception")

        except KeyboardInterrupt:
            logger.info("Runner stopped by user")


class BuildRunner(PhaseRunner):
    """Watches for pending requests, builds, transitions to built."""

    watch_status: StatusLiteral = "pending"
    stale_statuses = frozenset({STATUS_CLAIMED, STATUS_BUILDING, STATUS_BUILT})

    def execute_phase(self, request: TestRequest, request_path: Path) -> None:
        from autoforge.plugins.loader import load_component

        project_config = self.campaign.get("project", {})
        project_name = project_config.get("name", "dpdk")
        paths = self.config.get("paths", {})
        timeouts = self.config.get("timeouts", {})
        source_path = Path(paths.get("dpdk_src", "/opt/dpdk"))
        build_dir = Path(paths.get("build_dir", "/tmp/dpdk-build"))
        build_timeout = int(timeouts.get("build_minutes", 30)) * 60

        builder = load_component(project_name, "build", request.build_plugin)
        builder.configure(project_config, self.config)

        update_status(request, STATUS_BUILDING, request_path)
        build_result = builder.build(source_path, request.source_commit, build_dir, build_timeout)

        if not build_result.success:
            fail(request, request_path, error="Build failed", log_snippet=build_result.log)
            return

        update_status(request, STATUS_BUILT, request_path)


class DeployRunner(PhaseRunner):
    """Watches for built requests, deploys, transitions to deployed."""

    watch_status: StatusLiteral = "built"
    stale_statuses = frozenset({STATUS_DEPLOYING, STATUS_DEPLOYED})

    def execute_phase(self, request: TestRequest, request_path: Path) -> None:
        from autoforge.plugins.loader import load_component

        project_config = self.campaign.get("project", {})
        project_name = project_config.get("name", "dpdk")

        deployer = load_component(project_name, "deploy", request.deploy_plugin)
        deployer.configure(project_config, self.config)

        update_status(request, STATUS_DEPLOYING, request_path)

        # DeployRunner needs the build artifacts; for now read from request
        from autoforge.plugins.protocols import BuildResult

        build_result = BuildResult(
            success=True,
            log="",
            duration_seconds=0,
            artifacts={
                "build_dir": self.config.get("paths", {}).get("build_dir", "/tmp/dpdk-build"),
            },
        )
        deploy_result = deployer.deploy(build_result)

        if not deploy_result.success:
            fail(request, request_path, error=deploy_result.error or "Deploy failed")
            return

        update_status(request, STATUS_DEPLOYED, request_path)


class TestRunner(PhaseRunner):
    """Watches for deployed requests, tests, transitions to completed."""

    watch_status: StatusLiteral = "deployed"
    stale_statuses = frozenset({STATUS_RUNNING})

    def execute_phase(self, request: TestRequest, request_path: Path) -> None:
        from datetime import UTC, datetime

        from autoforge.plugins.loader import load_component
        from autoforge.plugins.protocols import DeployResult

        project_config = self.campaign.get("project", {})
        project_name = project_config.get("name", "dpdk")
        timeouts = self.config.get("timeouts", {})
        test_timeout = int(timeouts.get("test_minutes", 10)) * 60

        tester = load_component(project_name, "test", request.test_plugin)
        tester.configure(project_config, self.config)

        update_status(request, STATUS_RUNNING, request_path)

        deploy_result = DeployResult(
            success=True,
            target_info={
                "build_dir": self.config.get("paths", {}).get("build_dir", "/tmp/dpdk-build"),
            },
        )
        test_result = tester.test(deploy_result, timeout=test_timeout)

        if not test_result.success:
            fail(request, request_path, error=test_result.error or "Test failed")
            return

        from autoforge.protocol import STATUS_COMPLETED

        update_status(
            request,
            STATUS_COMPLETED,
            request_path,
            results_json=test_result.results_json,
            results_summary=test_result.results_summary,
            metric_value=test_result.metric_value,
            completed_at=datetime.now(UTC).isoformat(),
        )


class FullRunner(PhaseRunner):
    """Runs all phases sequentially (single-machine mode)."""

    watch_status: StatusLiteral = "pending"
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
        from datetime import UTC, datetime

        from autoforge.plugins.loader import load_component

        project_config = self.campaign.get("project", {})
        project_name = project_config.get("name", "dpdk")
        paths = self.config.get("paths", {})
        timeouts = self.config.get("timeouts", {})
        source_path = Path(paths.get("dpdk_src", "/opt/dpdk"))
        build_dir = Path(paths.get("build_dir", "/tmp/dpdk-build"))
        build_timeout = int(timeouts.get("build_minutes", 30)) * 60
        test_timeout = int(timeouts.get("test_minutes", 10)) * 60

        # Build
        builder = load_component(project_name, "build", request.build_plugin)
        builder.configure(project_config, self.config)
        update_status(request, STATUS_BUILDING, request_path)
        build_result = builder.build(source_path, request.source_commit, build_dir, build_timeout)

        if not build_result.success:
            fail(request, request_path, error="Build failed", log_snippet=build_result.log)
            return

        # Deploy
        deployer = load_component(project_name, "deploy", request.deploy_plugin)
        deployer.configure(project_config, self.config)
        update_status(request, STATUS_BUILT, request_path)
        update_status(request, STATUS_DEPLOYING, request_path)
        deploy_result = deployer.deploy(build_result)

        if not deploy_result.success:
            fail(request, request_path, error=deploy_result.error or "Deploy failed")
            return

        # Test
        tester = load_component(project_name, "test", request.test_plugin)
        tester.configure(project_config, self.config)
        update_status(request, STATUS_DEPLOYED, request_path)
        update_status(request, STATUS_RUNNING, request_path)
        test_result = tester.test(deploy_result, timeout=test_timeout)

        if not test_result.success:
            fail(request, request_path, error=test_result.error or "Test failed")
            return

        from autoforge.protocol import STATUS_COMPLETED

        update_status(
            request,
            STATUS_COMPLETED,
            request_path,
            results_json=test_result.results_json,
            results_summary=test_result.results_summary,
            metric_value=test_result.metric_value,
            completed_at=datetime.now(UTC).isoformat(),
        )
