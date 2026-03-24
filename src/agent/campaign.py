"""Campaign configuration type definitions."""

from __future__ import annotations

from typing import TypedDict


class MetricConfig(TypedDict, total=False):
    """Metric configuration from campaign TOML."""

    name: str
    path: str
    direction: str
    threshold: float


class TestConfig(TypedDict, total=False):
    """Test configuration from campaign TOML."""

    backend: str
    test_suites: list[str]
    test_cases: list[str]
    perf: bool


class AgentConfig(TypedDict, total=False):
    """Agent polling configuration from campaign TOML."""

    poll_interval: int
    timeout_minutes: int


class DpdkConfig(TypedDict, total=False):
    """DPDK submodule configuration from campaign TOML."""

    submodule_path: str
    optimization_branch: str
    scope: list[str]


class GoalConfig(TypedDict, total=False):
    """Goal description from campaign TOML."""

    description: str


class CampaignMeta(TypedDict, total=False):
    """Campaign metadata from campaign TOML."""

    name: str
    max_iterations: int


class ProfilingConfig(TypedDict, total=False):
    """Profiling configuration from campaign TOML."""

    enabled: bool


class CampaignConfig(TypedDict, total=False):
    """Full campaign configuration as loaded from TOML."""

    campaign: CampaignMeta
    metric: MetricConfig
    test: TestConfig
    agent: AgentConfig
    dpdk: DpdkConfig
    goal: GoalConfig
    profiling: ProfilingConfig
