"""Campaign configuration type definitions and loading."""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Literal, TypedDict

REPO_ROOT = Path(__file__).resolve().parent.parent
POINTER_PATH = REPO_ROOT / ".autoforge.toml"


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
    Callers should use campaign.get("section", {}) for optional sections,
    or direct subscript campaign["section"] for sections known to be present
    after validation.
    """

    campaign: CampaignMeta
    metric: MetricConfig
    agent: AgentConfig
    project: ProjectConfig
    goal: GoalConfig
    profiling: ProfilingConfig
    platform: PlatformConfig


class PointerConfig(TypedDict):
    """Contents of .autoforge.toml pointer file."""

    project: str
    sprint: str


def load_pointer(path: Path | None = None) -> PointerConfig:
    """Load the .autoforge.toml pointer file.

    Args:
        path: Override path. Defaults to REPO_ROOT/.autoforge.toml.

    Raises:
        FileNotFoundError: If the pointer file does not exist.
        KeyError: If required fields are missing.
    """
    pointer_path = path or POINTER_PATH
    with open(pointer_path, "rb") as f:
        data = tomllib.load(f)
    project = data.get("project")
    sprint = data.get("sprint", "")
    if not project:
        msg = f"Missing 'project' in {pointer_path}"
        raise KeyError(msg)
    return PointerConfig(project=project, sprint=sprint)


def save_pointer(project: str, sprint: str, path: Path | None = None) -> None:
    """Write or update the .autoforge.toml pointer file."""
    pointer_path = path or POINTER_PATH
    pointer_path.write_text(f'project = "{project}"\nsprint = "{sprint}"\n')


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
        return tomllib.load(f)
