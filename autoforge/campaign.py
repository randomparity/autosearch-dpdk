"""Campaign configuration type definitions and loading."""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Literal, TypedDict

from autoforge.config import resolve_vars
from autoforge.pointer import REPO_ROOT, load_pointer
from autoforge.protocol import Direction


class MetricConfig(TypedDict, total=False):
    """Metric configuration from campaign TOML."""

    name: str
    path: str
    direction: Literal["maximize", "minimize"]
    threshold: float


class AgentConfig(TypedDict, total=False):
    """Agent polling configuration from campaign TOML."""

    poll_interval: int
    timeout_minutes: int


class ProjectConfig(TypedDict, total=False):
    """Project-specific configuration from campaign TOML."""

    name: str
    build: str
    deploy: str
    test: str
    profiler: str
    judge: str
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


class PlatformConfig(TypedDict, total=False):
    """Platform configuration from campaign TOML."""

    arch: str


class CampaignConfig(TypedDict, total=False):
    """Full campaign configuration as loaded from TOML.

    Outer section keys use total=False because TOML files may omit sections.
    Use the accessor functions below (e.g. metric_direction, project_name)
    instead of raw .get() chains.
    """

    campaign: CampaignMeta
    metric: MetricConfig
    agent: AgentConfig
    project: ProjectConfig
    goal: GoalConfig
    profiling: ProfilingConfig
    platform: PlatformConfig


# --- Typed accessor functions ---
# These replace raw .get() chains throughout the codebase with
# a single location for default values and type annotations.


def metric_direction(cfg: CampaignConfig) -> Direction:
    """Return the metric direction from campaign config."""
    return cfg.get("metric", {}).get("direction", "maximize")


def metric_name(cfg: CampaignConfig) -> str:
    """Return the metric name from campaign config."""
    return cfg.get("metric", {}).get("name", "throughput")


def metric_threshold(cfg: CampaignConfig) -> float:
    """Return the metric threshold from campaign config."""
    return cfg.get("metric", {}).get("threshold", 0.0)


def metric_config(cfg: CampaignConfig) -> MetricConfig:
    """Return the metric config section."""
    return cfg.get("metric", {})


def project_name(cfg: CampaignConfig) -> str:
    """Return the project name from campaign config."""
    return cfg.get("project", {}).get("name", "dpdk")


def project_config(cfg: CampaignConfig) -> ProjectConfig:
    """Return the project config section."""
    return cfg.get("project", {})


def submodule_path(cfg: CampaignConfig) -> str:
    """Return the submodule path from campaign config."""
    return cfg.get("project", {}).get("submodule_path", "dpdk")


def optimization_branch(cfg: CampaignConfig) -> str:
    """Return the optimization branch from campaign config."""
    return cfg.get("project", {}).get("optimization_branch", "")


def agent_poll_interval(cfg: CampaignConfig) -> int:
    """Return the agent poll interval in seconds."""
    return cfg.get("agent", {}).get("poll_interval", 30)


def agent_timeout(cfg: CampaignConfig) -> int:
    """Return the agent timeout in seconds."""
    return cfg.get("agent", {}).get("timeout_minutes", 60) * 60


def campaign_max_iterations(cfg: CampaignConfig) -> int:
    """Return the max iterations from campaign config."""
    return cfg.get("campaign", {}).get("max_iterations", 50)


def campaign_name(cfg: CampaignConfig) -> str:
    """Return the campaign name."""
    return cfg.get("campaign", {}).get("name", "unnamed")


def campaign_meta(cfg: CampaignConfig) -> CampaignMeta:
    """Return the campaign metadata section."""
    return cfg.get("campaign", {})


def goal_description(cfg: CampaignConfig) -> str:
    """Return the goal description from campaign config."""
    return cfg.get("goal", {}).get("description", "").strip()


def goal_config(cfg: CampaignConfig) -> GoalConfig:
    """Return the goal config section."""
    return cfg.get("goal", {})


def judge_plugin(cfg: CampaignConfig) -> str | None:
    """Return the judge plugin name from campaign config, or None if not set."""
    return cfg.get("project", {}).get("judge") or None


def platform_arch(cfg: CampaignConfig) -> str | None:
    """Return the platform arch from campaign config, or None if unset."""
    return cfg.get("platform", {}).get("arch")


def platform_config(cfg: CampaignConfig) -> PlatformConfig:
    """Return the platform config section."""
    return cfg.get("platform", {})


def resolve_campaign_path(explicit: Path | None = None) -> Path:
    """Resolve the campaign TOML path using a 3-tier fallback.

    Resolution order:
        1. Explicit path (--campaign CLI flag)
        2. AUTOFORGE_CAMPAIGN environment variable
        3. .autoforge.toml pointer → projects/{project}/sprints/{sprint}/campaign.toml

    Raises:
        FileNotFoundError: If the resolved path does not exist.
        KeyError: If the pointer file has no active sprint configured.
    """
    if explicit is not None:
        if not explicit.exists():
            msg = f"Campaign config not found: {explicit}"
            raise FileNotFoundError(msg)
        return explicit

    env_path = os.environ.get("AUTOFORGE_CAMPAIGN")
    if env_path:
        p = Path(env_path)
        if not p.exists():
            msg = f"AUTOFORGE_CAMPAIGN path not found: {p}"
            raise FileNotFoundError(msg)
        return p

    pointer = load_pointer()
    if not pointer["sprint"]:
        msg = "No active sprint. Run 'autoforge sprint init <name>' first."
        raise KeyError(msg)
    campaign_path = (
        REPO_ROOT
        / "projects"
        / pointer["project"]
        / "sprints"
        / pointer["sprint"]
        / "campaign.toml"
    )
    if not campaign_path.exists():
        msg = (
            f"Campaign config not found: {campaign_path}\n"
            f"Check .autoforge.toml (project={pointer['project']!r}, "
            f"sprint={pointer['sprint']!r})"
        )
        raise FileNotFoundError(msg)
    return campaign_path


def load_campaign(path: Path | None = None) -> CampaignConfig:
    """Load and return the campaign TOML configuration.

    Args:
        path: Explicit path to the campaign TOML. If None, uses
              resolve_campaign_path() to find it.

    Raises:
        FileNotFoundError: If the config file does not exist.
        tomllib.TOMLDecodeError: If the file is not valid TOML.
    """
    config_path = path or resolve_campaign_path()
    with open(config_path, "rb") as f:
        try:
            data = tomllib.load(f)
        except tomllib.TOMLDecodeError as exc:
            raise ValueError(f"Invalid TOML in {config_path}: {exc}") from exc
    return resolve_vars(data)
