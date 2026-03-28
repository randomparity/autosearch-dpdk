"""Base class for phase-specific runners."""

from __future__ import annotations

import logging
import subprocess
import threading
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
from autoforge.git_utils import git_pull_with_stash
from autoforge.plugins.loader import load_component
from autoforge.plugins.protocols import BuildResult, DeployResult, RunnerConfig
from autoforge.pointer import REPO_ROOT
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
    config: RunnerConfig,
) -> BuildResult | None:
    """Execute the build phase. Returns BuildResult on success, None on failure."""
    proj_cfg = _project_config(campaign)
    proj_name = project_name(campaign)
    paths = config.get("paths", {})
    timeouts = config.get("timeouts", {})
    source_path = Path(paths.get("source_dir", "/opt/dpdk"))
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
            build_log_snippet=build_result.log,
            failed_phase="build",
        )
        return None

    update_status(request, STATUS_BUILT, request_path)
    return build_result


def _build_result_from_config(config: RunnerConfig) -> BuildResult:
    """Construct a BuildResult stub from runner config for split-runner mode."""
    paths = config.get("paths", {})
    return BuildResult(
        success=True,
        log="",
        duration_seconds=0,
        artifacts={"build_dir": paths.get("build_dir", "/tmp/build")},
    )


def _deploy_result_from_config(config: RunnerConfig) -> DeployResult:
    """Construct a DeployResult stub from runner config for split-runner mode."""
    paths = config.get("paths", {})
    return DeployResult(
        success=True,
        target_info={"build_dir": paths.get("build_dir", "/tmp/build")},
    )


def _run_deploy(
    request: TestRequest,
    request_path: Path,
    campaign: CampaignConfig,
    config: RunnerConfig,
    build_result: BuildResult | None = None,
) -> DeployResult | None:
    """Execute the deploy phase. Returns DeployResult on success, None on failure.

    When build_result is None (split-runner mode), a stub is constructed from config.
    """
    if build_result is None:
        build_result = _build_result_from_config(config)

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


def _prepare_profiler(
    campaign: CampaignConfig,
    config: RunnerConfig,
    deploy_result: DeployResult,
) -> tuple[Any, int, dict[str, Any]] | None:
    """Load profiler plugin and config. Returns (profiler, duration, config) or None."""
    profiling_cfg = campaign.get("profiling", {})
    if not profiling_cfg.get("enabled", False):
        return None

    proj_cfg = _project_config(campaign)
    proj_name = project_name(campaign)
    profiler_name = proj_cfg.get("profiler")
    if not profiler_name:
        logger.debug("Profiling enabled but no profiler plugin configured")
        return None

    try:
        profiler = load_component(
            proj_name,
            "profiler",
            profiler_name,
            project_config=proj_cfg,
            runner_config=config,
        )
    except (FileNotFoundError, ValueError) as exc:
        logger.warning("Failed to load profiler %r: %s", profiler_name, exc)
        return None

    duration = int(profiling_cfg.get("duration", 30))
    startup_delay = int(profiling_cfg.get("startup_delay", 5))
    profile_config: dict[str, Any] = {
        **deploy_result.target_info,
        "startup_delay": startup_delay,
    }
    return profiler, duration, profile_config


def _run_profile_thread(
    profiler: Any,
    duration: int,
    profile_config: dict[str, Any],
    result_holder: list[dict[str, Any] | None],
) -> None:
    """Run profiler in a background thread with a startup delay.

    Waits for the benchmark to generate load before capturing a profile.
    The result is stored in result_holder[0].
    """
    startup_delay = profile_config.pop("startup_delay", 5)
    profiler_name = getattr(profiler, "name", "unknown")

    logger.info(
        "Profiler %r waiting %ds for benchmark warmup",
        profiler_name,
        startup_delay,
    )
    time.sleep(startup_delay)

    logger.info("Running profiler %r for %ds", profiler_name, duration)
    try:
        result = profiler.profile(pid=0, duration=duration, config=profile_config)
    except Exception:
        logger.exception("Profiler %r raised an exception", profiler_name)
        return

    if not result.success:
        logger.warning("Profiler %r failed: %s", profiler_name, result.error)
        return

    logger.info(
        "Profiling complete (%s, %.1fs)",
        profiler_name,
        result.duration_seconds,
    )
    result_holder[0] = result.summary


def _cleanup_deploy_target(deploy_result: DeployResult) -> None:
    """Remove the deploy target (container) after test and profiling complete."""
    target_info = deploy_result.target_info
    container_name = target_info.get("container_name")
    if not container_name:
        return
    runtime = target_info.get("runtime", "docker")
    try:
        subprocess.run(
            [runtime, "rm", "-f", container_name],
            capture_output=True,
            timeout=30,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        logger.warning("Failed to clean up container %s", container_name)


def _run_test(
    request: TestRequest,
    request_path: Path,
    campaign: CampaignConfig,
    config: RunnerConfig,
    deploy_result: DeployResult | None = None,
) -> None:
    """Execute the test phase and update request to completed/failed.

    When deploy_result is None (split-runner mode), a stub is constructed from config.
    Profiling runs concurrently with the test in a background thread.
    """
    if deploy_result is None:
        deploy_result = _deploy_result_from_config(config)
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

    # Prepare profiler before starting the test so it can run concurrently.
    profiler_setup = _prepare_profiler(campaign, config, deploy_result)
    profile_result: list[dict[str, Any] | None] = [None]
    profile_thread: threading.Thread | None = None

    update_status(request, STATUS_RUNNING, request_path)

    # Start profiler thread before the test so it captures during benchmark execution.
    if profiler_setup is not None:
        profiler, duration, profile_config = profiler_setup
        profile_thread = threading.Thread(
            target=_run_profile_thread,
            args=(profiler, duration, profile_config, profile_result),
            daemon=True,
        )
        profile_thread.start()

    try:
        test_result = tester.test(deploy_result, timeout=test_timeout)
    finally:
        # Wait for profiler to finish (it may already be done).
        if profile_thread is not None:
            profile_thread.join(timeout=60)
        # Clean up the deploy target (e.g. container) after profiling completes.
        _cleanup_deploy_target(deploy_result)

    if not test_result.success:
        fail(
            request,
            request_path,
            error=test_result.error or "Test failed",
            test_log_snippet=test_result.log or None,
            failed_phase="test",
        )
        return

    results_json = test_result.results_json or {}
    if profile_result[0] is not None:
        results_json["profile"] = profile_result[0]

    complete_request(
        request,
        request_path,
        results_json=results_json,
        results_summary=test_result.results_summary,
        metric_value=test_result.metric_value,
    )


class PhaseRunner(ABC):
    """Base class for runners that handle specific pipeline phases."""

    watch_status: StatusLiteral
    stale_statuses: frozenset[str]

    def __init__(
        self,
        config: RunnerConfig,
        campaign: CampaignConfig,
        requests_dir: Path,
    ) -> None:
        self.config = config
        self.campaign = campaign
        self.requests_dir = requests_dir
        self.runner_id = config.get("runner", {}).get("runner_id", "")
        self.poll_interval = int(config.get("runner", {}).get("poll_interval", 30))

    needs_claim: bool = False
    """Whether this runner must claim requests before executing."""

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
                if not git_pull_with_stash(REPO_ROOT, timeout=GIT_TIMEOUT):
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

                if self.needs_claim and not claim(request, request_path):
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
    needs_claim: bool = True
    stale_statuses = frozenset({STATUS_CLAIMED, STATUS_BUILDING, STATUS_BUILT})

    def execute_phase(self, request: TestRequest, request_path: Path) -> None:
        _run_build(request, request_path, self.campaign, self.config)


class DeployRunner(PhaseRunner):
    """Watches for built requests, deploys, transitions to deployed."""

    watch_status: StatusLiteral = STATUS_BUILT
    stale_statuses = frozenset({STATUS_DEPLOYING, STATUS_DEPLOYED})

    def execute_phase(self, request: TestRequest, request_path: Path) -> None:
        _run_deploy(request, request_path, self.campaign, self.config)


class TestRunner(PhaseRunner):
    """Watches for deployed requests, tests, transitions to completed."""

    watch_status: StatusLiteral = STATUS_DEPLOYED
    stale_statuses = frozenset({STATUS_RUNNING})

    def execute_phase(self, request: TestRequest, request_path: Path) -> None:
        _run_test(request, request_path, self.campaign, self.config)


class FullRunner(PhaseRunner):
    """Runs all phases sequentially (single-machine mode)."""

    watch_status: StatusLiteral = STATUS_PENDING
    needs_claim: bool = True
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
