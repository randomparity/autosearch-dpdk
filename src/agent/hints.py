"""Architecture-specific optimization hints lookup."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.agent.campaign import CampaignConfig

KNOWN_ARCHES: frozenset[str] = frozenset(
    {
        "x86_64",
        "ppc64le",
        "aarch64",
        "s390x",
    }
)

HINTS_DIR = Path(__file__).resolve().parent.parent.parent / "docs" / "arch-hints"


def hints_path(arch: str) -> Path:
    """Return the path to the arch hints file.

    Args:
        arch: Architecture identifier (e.g. "ppc64le").

    Raises:
        ValueError: If arch is not recognized.
        FileNotFoundError: If the hints file does not exist.
    """
    if arch not in KNOWN_ARCHES:
        msg = f"Unknown arch {arch!r}. Known: {', '.join(sorted(KNOWN_ARCHES))}"
        raise ValueError(msg)
    path = HINTS_DIR / f"{arch}.md"
    if not path.exists():
        msg = f"No hints file for {arch!r} at {path}"
        raise FileNotFoundError(msg)
    return path


def hints_summary(arch: str) -> str:
    """Return a short summary pointing the agent to the hints file.

    Args:
        arch: Architecture identifier.

    Returns:
        Multi-line string with the file path and reading instructions.
    """
    path = hints_path(arch)
    line_count = sum(1 for _ in path.open())
    return (
        f"Architecture hints for {arch}: {path}\n"
        f"({line_count} lines — read this file for"
        f" optimization guidance)"
    )


def resolve_arch(campaign: CampaignConfig) -> str | None:
    """Extract arch from campaign config.

    Args:
        campaign: Parsed campaign TOML dict.

    Returns:
        Architecture string, or None if not configured.
    """
    return campaign.get("platform", {}).get("arch")
