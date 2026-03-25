"""Local deployer — trivial pass-through for bare-metal builds."""

from __future__ import annotations

from typing import Any

from autoforge.plugins.protocols import BuildResult, DeployResult


class LocalDeployer:
    """Build and test on the same machine — deploy is a no-op."""

    name = "local"

    def configure(self, project_config: dict[str, Any], runner_config: dict[str, Any]) -> None:
        pass

    def deploy(self, build_result: BuildResult) -> DeployResult:
        return DeployResult(
            success=True,
            target_info=build_result.artifacts,
        )
