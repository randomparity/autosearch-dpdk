"""Plugin protocol definitions and shared result dataclasses."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable


@dataclass
class BuildResult:
    """Result of a build phase."""

    success: bool
    log: str
    duration_seconds: float
    artifacts: dict[str, Any] = field(default_factory=dict)


@dataclass
class DeployResult:
    """Result of a deploy phase."""

    success: bool
    error: str | None = None
    target_info: dict[str, Any] = field(default_factory=dict)


@dataclass
class TestResult:
    """Result of a test phase."""

    __test__ = False

    success: bool
    metric_value: float | None
    results_json: dict[str, Any] | None
    results_summary: str | None
    error: str | None
    duration_seconds: float


@runtime_checkable
class Builder(Protocol):
    """Builds a project from source at a given commit."""

    def configure(self, project_config: dict[str, Any], runner_config: dict[str, Any]) -> None:
        """Store configuration for subsequent build calls."""
        ...

    def build(
        self, source_path: Path, commit: str, build_dir: Path, timeout: int
    ) -> BuildResult:
        """Build the project and return the result."""
        ...


@runtime_checkable
class Deployer(Protocol):
    """Deploys build artifacts to a test target."""

    def configure(self, project_config: dict[str, Any], runner_config: dict[str, Any]) -> None:
        """Store configuration for subsequent deploy calls."""
        ...

    def deploy(self, build_result: BuildResult) -> DeployResult:
        """Deploy build artifacts and return the result."""
        ...


@runtime_checkable
class Tester(Protocol):
    """Runs performance tests against a deployed target."""

    def configure(self, project_config: dict[str, Any], runner_config: dict[str, Any]) -> None:
        """Store configuration for subsequent test calls."""
        ...

    def test(self, deploy_result: DeployResult, timeout: int) -> TestResult:
        """Run tests and return the result."""
        ...


@runtime_checkable
class Plugin(Protocol):
    """A project plugin that provides builder, deployer, and tester."""

    name: str

    def create_builder(self) -> Builder:
        """Create a new builder instance."""
        ...

    def create_deployer(self) -> Deployer:
        """Create a new deployer instance."""
        ...

    def create_tester(self) -> Tester:
        """Create a new tester instance."""
        ...
