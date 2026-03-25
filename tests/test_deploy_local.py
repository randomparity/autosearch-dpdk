"""Tests for the local deployer plugin."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from autoforge.plugins import Deployer
from autoforge.plugins.protocols import BuildResult, DeployResult

PLUGIN_PATH = Path(__file__).parent.parent / "projects" / "dpdk" / "deploys" / "local.py"
MODULE_NAME = "autoforge_plugin_deploy_local"


def _load_deployer_module():
    spec = importlib.util.spec_from_file_location(MODULE_NAME, PLUGIN_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[MODULE_NAME] = mod
    spec.loader.exec_module(mod)
    return mod


_mod = _load_deployer_module()
LocalDeployer = _mod.LocalDeployer


class TestLocalDeployer:
    def test_returns_success(self) -> None:
        deployer = LocalDeployer()
        deployer.configure({}, {})
        build_result = BuildResult(
            success=True,
            log="ok",
            duration_seconds=1.0,
        )
        result = deployer.deploy(build_result)
        assert isinstance(result, DeployResult)
        assert result.success is True

    def test_forwards_artifacts(self) -> None:
        deployer = LocalDeployer()
        deployer.configure({}, {})
        artifacts = {"build_dir": "/tmp/dpdk-build", "extra": "value"}
        build_result = BuildResult(
            success=True,
            log="ok",
            duration_seconds=1.0,
            artifacts=artifacts,
        )
        result = deployer.deploy(build_result)
        assert result.target_info == artifacts

    def test_conforms_to_protocol(self) -> None:
        deployer = LocalDeployer()
        assert isinstance(deployer, Deployer)
