"""Plugin system for autoforge — protocols, result types, and discovery."""

from __future__ import annotations

from autoforge.plugins.loader import (
    PipelineComponents,
    list_components,
    load_component,
    load_pipeline,
)
from autoforge.plugins.protocols import (
    Builder,
    BuildResult,
    Deployer,
    DeployResult,
    Profiler,
    ProfileResult,
    Tester,
    TestResult,
)

__all__ = [
    "BuildResult",
    "Builder",
    "DeployResult",
    "Deployer",
    "PipelineComponents",
    "ProfileResult",
    "Profiler",
    "TestResult",
    "Tester",
    "list_components",
    "load_component",
    "load_pipeline",
]
