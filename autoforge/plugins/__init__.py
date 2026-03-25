"""Plugin system for autoforge — protocols, result types, and discovery."""

from __future__ import annotations

from autoforge.plugins.loader import list_plugins, load_plugin
from autoforge.plugins.protocols import (
    Builder,
    BuildResult,
    Deployer,
    DeployResult,
    Plugin,
    Tester,
    TestResult,
)

__all__ = [
    "BuildResult",
    "Builder",
    "DeployResult",
    "Deployer",
    "Plugin",
    "TestResult",
    "Tester",
    "list_plugins",
    "load_plugin",
]
