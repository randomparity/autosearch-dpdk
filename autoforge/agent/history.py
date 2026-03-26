"""TSV-based iteration history management."""

from __future__ import annotations

import csv
import logging
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

COLUMNS = [
    "sequence",
    "timestamp",
    "source_commit",
    "metric_value",
    "status",
    "description",
    "tags",
]
FAILURE_COLUMNS = ["timestamp", "source_commit", "metric_value", "description", "diff_summary"]


def append_result(
    seq: int,
    commit: str,
    metric: float | None,
    status: str,
    description: str,
    path: Path,
    *,
    tags: list[str] | None = None,
) -> None:
    """Append an iteration result to the TSV history file.

    Args:
        seq: Iteration sequence number.
        commit: DPDK submodule commit SHA.
        metric: Metric value (None if the run failed before measurement).
        status: Final status (completed, failed, etc.).
        description: Human-readable description of the change.
        path: Path to the results.tsv file.
        tags: Optional experiment category tags.
    """
    existing = load_history(path)
    if any(row.get("sequence") == str(seq) for row in existing):
        logger.info("Sequence %d already recorded in %s, skipping", seq, path)
        return

    timestamp = datetime.now(UTC).isoformat()
    metric_str = str(metric) if metric is not None else ""

    try:
        with open(path, "a", newline="") as f:
            writer = csv.writer(f, delimiter="\t")
            tags_str = ",".join(tags) if tags else ""
            writer.writerow([seq, timestamp, commit, metric_str, status, description, tags_str])
    except OSError as exc:
        msg = f"Failed to append result to {path}: {exc}"
        raise OSError(msg) from exc


def load_history(path: Path) -> list[dict]:
    """Read the TSV history file into a list of dicts.

    Args:
        path: Path to the results.tsv file.

    Returns:
        List of row dicts keyed by column name.
    """
    if not path.exists():
        return []

    try:
        with open(path, newline="") as f:
            reader = csv.DictReader(f, delimiter="\t")
            return list(reader)
    except OSError as exc:
        logger.warning("Failed to read history from %s: %s", path, exc)
        return []


def best_result(
    path: Path,
    direction: str = "maximize",
) -> dict | None:
    """Return the history row with the best metric value.

    Args:
        path: Path to the results.tsv file.
        direction: 'maximize' or 'minimize'.

    Returns:
        The best row dict, or None if no valid metrics exist.
    """
    rows = load_history(path)
    scored = []
    for row in rows:
        val = row.get("metric_value", "")
        if val:
            try:
                scored.append((float(val), row))
            except ValueError:
                continue

    if not scored:
        return None

    if direction == "minimize":
        return min(scored, key=lambda x: x[0])[1]
    return max(scored, key=lambda x: x[0])[1]


def append_failure(
    commit: str,
    metric: float | None,
    description: str,
    diff_summary: str,
    path: Path,
) -> None:
    """Record a failed optimization attempt.

    Args:
        commit: DPDK commit SHA that was reverted.
        metric: Metric value that was worse than best.
        description: What the change attempted.
        diff_summary: Short git diff --stat of the reverted change.
        path: Path to the failures.tsv file.
    """
    timestamp = datetime.now(UTC).isoformat()
    metric_str = str(metric) if metric is not None else ""

    try:
        write_header = not path.exists()
        with open(path, "a", newline="") as f:
            writer = csv.writer(f, delimiter="\t")
            if write_header:
                writer.writerow(FAILURE_COLUMNS)
            writer.writerow([timestamp, commit, metric_str, description, diff_summary])
    except OSError as exc:
        msg = f"Failed to append failure to {path}: {exc}"
        raise OSError(msg) from exc


def load_failures(path: Path) -> list[dict]:
    """Read the failures TSV file.

    Args:
        path: Path to the failures.tsv file.

    Returns:
        List of row dicts keyed by column name.
    """
    if not path.exists():
        return []

    try:
        with open(path, newline="") as f:
            reader = csv.DictReader(f, delimiter="\t")
            return list(reader)
    except OSError as exc:
        logger.warning("Failed to read failures from %s: %s", path, exc)
        return []


def format_failures(failures: list[dict], limit: int = 10) -> str:
    """Format recent failures for inclusion in prompts.

    Args:
        failures: List of failure row dicts.
        limit: Maximum number of recent failures to include.

    Returns:
        Multi-line string summarizing recent failures.
    """
    if not failures:
        return ""

    recent = failures[-limit:]
    lines = ["Previously failed attempts (do NOT repeat these):"]
    for row in recent:
        desc = row.get("description", "?")
        metric = row.get("metric_value", "N/A") or "N/A"
        diff = row.get("diff_summary", "")
        lines.append(f"  - {desc} (metric={metric})")
        if diff:
            for diff_line in diff.split("\\n"):
                if diff_line.strip():
                    lines.append(f"    {diff_line.strip()}")

    return "\n".join(lines)
