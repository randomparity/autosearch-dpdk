"""Sprint lifecycle management and path derivation."""

from __future__ import annotations

import csv
import logging
import re
import shutil
from pathlib import Path

from autoforge.campaign import REPO_ROOT, load_pointer, save_pointer

logger = logging.getLogger(__name__)

SPRINT_NAME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-[a-z0-9][a-z0-9-]*$")

RESULTS_COLUMNS = [
    "sequence",
    "timestamp",
    "source_commit",
    "metric_value",
    "status",
    "description",
    "tags",
]


def _sprints_root(project: str | None = None) -> Path:
    """Derive the sprints root from project name.

    Sprints live under projects/<name>/sprints/ when a project name is set.
    """
    if project:
        return REPO_ROOT / "projects" / project / "sprints"
    return REPO_ROOT / "sprints"


def _sprints_root_from_pointer() -> tuple[Path, str]:
    """Load pointer and return (sprints_root, project_name)."""
    pointer = load_pointer()
    return _sprints_root(pointer["project"]), pointer["project"]


def validate_sprint_name(name: str) -> None:
    """Raise ValueError if name doesn't match YYYY-MM-DD-slug format."""
    if not SPRINT_NAME_RE.match(name):
        msg = (
            f"Invalid sprint name {name!r}. "
            "Must match YYYY-MM-DD-slug (lowercase alphanumeric + hyphens)."
        )
        raise ValueError(msg)


def active_sprint_name() -> str:
    """Return the active sprint name from the .autoforge.toml pointer.

    Raises:
        KeyError: If no sprint is configured.
        FileNotFoundError: If pointer file doesn't exist.
    """
    pointer = load_pointer()
    if not pointer["sprint"]:
        msg = "No active sprint. Run 'autoforge sprint init <name>' first."
        raise KeyError(msg)
    return pointer["sprint"]


def sprint_dir() -> Path:
    """Return the sprint root directory."""
    pointer = load_pointer()
    if not pointer["sprint"]:
        msg = "No active sprint. Run 'autoforge sprint init <name>' first."
        raise KeyError(msg)
    return _sprints_root(pointer["project"]) / pointer["sprint"]


def requests_dir() -> Path:
    """Return the sprint's requests directory."""
    return sprint_dir() / "requests"


def results_path() -> Path:
    """Return the sprint's results.tsv path."""
    return sprint_dir() / "results.tsv"


def failures_path() -> Path:
    """Return the sprint's failures.tsv path."""
    return sprint_dir() / "failures.tsv"


def docs_dir() -> Path:
    """Return the sprint's docs directory."""
    return sprint_dir() / "docs"


def init_sprint(
    name: str,
    template: Path | None = None,
    from_sprint: str | None = None,
) -> Path:
    """Create a new sprint directory with campaign config.

    The campaign.toml is copied from one of:
        1. ``from_sprint`` — an existing sprint's campaign.toml
        2. ``template`` — an explicit template path
        3. Default: config/campaign.toml.example

    Args:
        name: Sprint name (YYYY-MM-DD-slug format).
        template: Path to a campaign TOML template.
        from_sprint: Name of an existing sprint to clone config from.

    Returns:
        Path to the created sprint directory.
    """
    validate_sprint_name(name)

    root, project = _sprints_root_from_pointer()
    sdir = root / name
    if sdir.exists():
        msg = f"Sprint directory already exists: {sdir}"
        raise FileExistsError(msg)

    (sdir / "requests").mkdir(parents=True)
    (sdir / "docs").mkdir()

    # Determine source for campaign.toml
    if from_sprint:
        validate_sprint_name(from_sprint)
        source = root / from_sprint / "campaign.toml"
        if not source.exists():
            msg = f"Source sprint config not found: {source}"
            raise FileNotFoundError(msg)
    elif template:
        source = template
    else:
        source = REPO_ROOT / "config" / "campaign.toml.example"
        if not source.exists():
            msg = f"Template not found: {source}"
            raise FileNotFoundError(msg)

    shutil.copy2(source, sdir / "campaign.toml")

    # Empty results.tsv with header
    with open(sdir / "results.tsv", "w", newline="") as f:
        writer = csv.writer(f, delimiter="\t")
        writer.writerow(RESULTS_COLUMNS)

    # Activate the new sprint via pointer
    save_pointer(project, name)

    return sdir


def switch_sprint(name: str) -> None:
    """Switch the active sprint to an existing one.

    Args:
        name: Sprint name to switch to.
    """
    validate_sprint_name(name)
    root, project = _sprints_root_from_pointer()
    sdir = root / name
    if not sdir.is_dir():
        msg = f"Sprint not found: {sdir}"
        raise FileNotFoundError(msg)
    save_pointer(project, name)


def list_sprints() -> list[dict]:
    """Return summary info for all sprints.

    Returns:
        List of dicts with 'name', 'iterations', 'max_metric' keys,
        sorted by name (chronological). max_metric is always the
        maximum value regardless of campaign direction.
    """
    try:
        root, _ = _sprints_root_from_pointer()
    except (FileNotFoundError, KeyError):
        return []

    if not root.is_dir():
        return []

    sprints = []
    for d in sorted(root.iterdir()):
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
            except OSError as exc:
                logger.warning("Failed to read TSV for sprint %s: %s", d.name, exc)

        sprints.append(info)

    return sprints
