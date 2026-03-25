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

TOPIC_SUFFIXES: dict[str, str] = {
    "optimization": "",
    "perf-counters": "-perf-counters",
}

DEFAULT_TOPIC = "optimization"

HINTS_DIR = Path(__file__).resolve().parent.parent.parent / "docs" / "arch-hints"


def hints_path(arch: str, topic: str = DEFAULT_TOPIC) -> Path:
    """Return the path to the arch hints file.

    Args:
        arch: Architecture identifier (e.g. "ppc64le").
        topic: Hint topic (e.g. "optimization", "perf-counters").

    Raises:
        ValueError: If arch or topic is not recognized.
        FileNotFoundError: If the hints file does not exist.
    """
    if arch not in KNOWN_ARCHES:
        msg = f"Unknown arch {arch!r}. Known: {', '.join(sorted(KNOWN_ARCHES))}"
        raise ValueError(msg)
    if topic not in TOPIC_SUFFIXES:
        msg = (
            f"Unknown topic {topic!r}."
            f" Known: {', '.join(sorted(TOPIC_SUFFIXES))}"
        )
        raise ValueError(msg)
    suffix = TOPIC_SUFFIXES[topic]
    path = HINTS_DIR / f"{arch}{suffix}.md"
    if not path.exists():
        msg = f"No {topic} hints for {arch!r} at {path}"
        raise FileNotFoundError(msg)
    return path


def hints_summary(arch: str, topic: str = DEFAULT_TOPIC) -> str:
    """Return a short summary pointing the agent to the hints file.

    Args:
        arch: Architecture identifier.
        topic: Hint topic.

    Returns:
        Multi-line string with the file path and reading instructions.
    """
    path = hints_path(arch, topic)
    with path.open() as fh:
        line_count = sum(1 for _ in fh)
    return (
        f"Architecture {topic} hints for {arch}: {path}\n"
        f"({line_count} lines — read this file for"
        f" {topic} guidance)"
    )


def list_topics(arch: str) -> list[str]:
    """Return available hint topics for an architecture.

    Args:
        arch: Architecture identifier.

    Raises:
        ValueError: If arch is not recognized.
    """
    if arch not in KNOWN_ARCHES:
        msg = f"Unknown arch {arch!r}. Known: {', '.join(sorted(KNOWN_ARCHES))}"
        raise ValueError(msg)
    topics = []
    for topic_name, suffix in TOPIC_SUFFIXES.items():
        path = HINTS_DIR / f"{arch}{suffix}.md"
        if path.exists():
            topics.append(topic_name)
    return sorted(topics)


def resolve_arch(campaign: CampaignConfig) -> str | None:
    """Extract arch from campaign config.

    Args:
        campaign: Parsed campaign TOML dict.

    Returns:
        Architecture string, or None if not configured.
    """
    return campaign.get("platform", {}).get("arch")
