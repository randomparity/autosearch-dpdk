"""Sprint lifecycle management and path derivation."""

from __future__ import annotations

import csv
import re
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from autoforge.agent.campaign import CampaignConfig

SPRINTS_ROOT = Path(__file__).resolve().parent.parent.parent / "sprints"
SPRINT_NAME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-[a-z0-9][a-z0-9-]*$")

RESULTS_COLUMNS = [
    "sequence",
    "timestamp",
    "source_commit",
    "metric_value",
    "status",
    "description",
]


def validate_sprint_name(name: str) -> None:
    """Raise ValueError if name doesn't match YYYY-MM-DD-slug format."""
    if not SPRINT_NAME_RE.match(name):
        msg = (
            f"Invalid sprint name {name!r}. "
            "Must match YYYY-MM-DD-slug (lowercase alphanumeric + hyphens)."
        )
        raise ValueError(msg)


def active_sprint_name(campaign: CampaignConfig) -> str:
    """Return the active sprint name from campaign config.

    Raises:
        KeyError: If no sprint is configured.
    """
    name = campaign.get("sprint", {}).get("name")
    if not name:
        msg = "No active sprint. Run 'autoforge sprint init <name>' first."
        raise KeyError(msg)
    return name


def sprint_dir(campaign: CampaignConfig) -> Path:
    """Return the sprint root directory."""
    return SPRINTS_ROOT / active_sprint_name(campaign)


def requests_dir(campaign: CampaignConfig) -> Path:
    """Return the sprint's requests directory."""
    return sprint_dir(campaign) / "requests"


def results_path(campaign: CampaignConfig) -> Path:
    """Return the sprint's results.tsv path."""
    return sprint_dir(campaign) / "results.tsv"


def failures_path(campaign: CampaignConfig) -> Path:
    """Return the sprint's failures.tsv path."""
    return sprint_dir(campaign) / "failures.tsv"


def docs_dir(campaign: CampaignConfig) -> Path:
    """Return the sprint's docs directory."""
    return sprint_dir(campaign) / "docs"


def init_sprint(name: str, campaign_path: Path) -> Path:
    """Create a new sprint directory with frozen campaign config.

    Args:
        name: Sprint name (YYYY-MM-DD-slug format).
        campaign_path: Path to the live campaign.toml.

    Returns:
        Path to the created sprint directory.
    """
    validate_sprint_name(name)

    sdir = SPRINTS_ROOT / name
    if sdir.exists():
        msg = f"Sprint directory already exists: {sdir}"
        raise FileExistsError(msg)

    (sdir / "requests").mkdir(parents=True)
    (sdir / "docs").mkdir()

    # Frozen snapshot of campaign config
    shutil.copy2(campaign_path, sdir / "campaign.toml")

    # Empty results.tsv with header
    with open(sdir / "results.tsv", "w", newline="") as f:
        writer = csv.writer(f, delimiter="\t")
        writer.writerow(RESULTS_COLUMNS)

    # Write sprint name into live campaign.toml
    _set_sprint_name(campaign_path, name)

    return sdir


def switch_sprint(name: str, campaign_path: Path) -> None:
    """Switch the active sprint to an existing one.

    Args:
        name: Sprint name to switch to.
        campaign_path: Path to the live campaign.toml.
    """
    validate_sprint_name(name)
    sdir = SPRINTS_ROOT / name
    if not sdir.is_dir():
        msg = f"Sprint not found: {sdir}"
        raise FileNotFoundError(msg)
    _set_sprint_name(campaign_path, name)


def list_sprints() -> list[dict]:
    """Return summary info for all sprints.

    Returns:
        List of dicts with 'name', 'iterations', 'max_metric' keys,
        sorted by name (chronological). max_metric is always the
        maximum value regardless of campaign direction.
    """
    if not SPRINTS_ROOT.is_dir():
        return []

    sprints = []
    for d in sorted(SPRINTS_ROOT.iterdir()):
        if not d.is_dir() or not SPRINT_NAME_RE.match(d.name):
            continue

        info: dict = {"name": d.name, "iterations": 0, "max_metric": None}
        tsv = d / "results.tsv"
        if tsv.exists():
            try:
                with open(tsv, newline="") as f:
                    reader = csv.DictReader(f, delimiter="\t")
                    rows = list(reader)
                info["iterations"] = len(rows)
                metrics = []
                for row in rows:
                    val = row.get("metric_value", "")
                    if val:
                        try:
                            metrics.append(float(val))
                        except ValueError:
                            continue
                if metrics:
                    info["max_metric"] = max(metrics)
            except OSError:
                pass

        sprints.append(info)

    return sprints


def _set_sprint_name(campaign_path: Path, name: str) -> None:
    """Write or update [sprint] name in campaign.toml.

    Handles comments and blank lines between [sprint] and name =.
    """
    lines = campaign_path.read_text().splitlines(keepends=True)
    sprint_line = f'name = "{name}"\n'

    # Find [sprint] section and replace the next name = line
    in_sprint = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped == "[sprint]":
            in_sprint = True
            continue
        if in_sprint:
            if stripped.startswith("[") and stripped.endswith("]"):
                # Hit next section without finding name — insert before it
                lines.insert(i, sprint_line)
                break
            if stripped.startswith("name") and "=" in stripped:
                lines[i] = sprint_line
                break
    else:
        if not in_sprint:
            # No [sprint] section — prepend one
            lines.insert(0, "[sprint]\n")
            lines.insert(1, sprint_line)
            lines.insert(2, "\n")

    campaign_path.write_text("".join(lines))
