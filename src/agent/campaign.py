"""Campaign configuration type definitions and loading."""

from __future__ import annotations

import tomllib
from pathlib import Path
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


class SprintConfig(TypedDict, total=False):
    """Active sprint from campaign TOML."""

    name: str


class PlatformConfig(TypedDict, total=False):
    """Platform configuration from campaign TOML."""

    arch: str


class CampaignConfig(TypedDict, total=False):
    """Full campaign configuration as loaded from TOML."""

    campaign: CampaignMeta
    metric: MetricConfig
    test: TestConfig
    agent: AgentConfig
    dpdk: DpdkConfig
    goal: GoalConfig
    profiling: ProfilingConfig
    sprint: SprintConfig
    platform: PlatformConfig


def load_campaign(path: Path | None = None) -> CampaignConfig:
    """Load and return the campaign TOML configuration.

    Args:
        path: Path to the campaign TOML. Defaults to config/campaign.toml.

    Raises:
        FileNotFoundError: If the config file does not exist.
        tomllib.TOMLDecodeError: If the file is not valid TOML.
    """
    config_path = path or Path(__file__).resolve().parent.parent.parent / "config" / "campaign.toml"
    with open(config_path, "rb") as f:
        return tomllib.load(f)
