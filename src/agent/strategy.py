"""Context formatting and change validation for the optimization loop."""

from __future__ import annotations

import logging
import subprocess
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)


def format_context(history: list[dict], campaign: dict) -> str:
    """Build a prompt-friendly summary of campaign state and history.

    Args:
        history: List of row dicts from load_history().
        campaign: Parsed campaign.toml as a dict.

    Returns:
        A multi-line string suitable for display or prompt injection.
    """
    metric_cfg = campaign.get("metric", {})
    dpdk_cfg = campaign.get("dpdk", {})
    campaign_cfg = campaign.get("campaign", {})

    goal = campaign.get("goal", {}).get("description", "").strip()

    lines = [
        f"Campaign: {campaign_cfg.get('name', 'unnamed')}",
        f"Objective: {metric_cfg.get('direction', 'maximize')} {metric_cfg.get('name', '?')}",
        f"DPDK scope: {', '.join(dpdk_cfg.get('scope', []))}",
        f"Iterations: {len(history)} / {campaign_cfg.get('max_iterations', '?')}",
        "",
    ]

    if goal:
        lines.append(f"Goal: {goal}")
        lines.append("")

    scored = _scored_rows(history)
    if scored:
        best_val, best_row = max(scored, key=lambda x: x[0])
        if metric_cfg.get("direction") == "minimize":
            best_val, best_row = min(scored, key=lambda x: x[0])
        lines.append(f"Best so far: {best_val} ({best_row.get('description', '?')})")
    else:
        lines.append("No successful iterations yet.")

    lines.append("")
    recent = history[-5:] if len(history) > 5 else history
    if recent:
        lines.append("Recent attempts:")
        for row in recent:
            metric = row.get("metric_value", "N/A") or "N/A"
            status = row.get("status", "?")
            desc = row.get("description", "?")
            lines.append(f"  #{row.get('sequence', '?')} [{status}] metric={metric} — {desc}")

    return "\n".join(lines)


def _scored_rows(history: list[dict]) -> list[tuple[float, dict]]:
    """Extract rows with valid numeric metric values."""
    scored = []
    for row in history:
        val = row.get("metric_value", "")
        if val:
            try:
                scored.append((float(val), row))
            except ValueError:
                continue
    return scored


def validate_change(dpdk_path: Path) -> bool:
    """Check whether the DPDK submodule has a new commit staged or committed.

    Runs `git -C <dpdk_path> diff --cached --stat` to check for staged changes,
    and `git -C <dpdk_path> log -1 --oneline` to confirm a commit exists.

    Args:
        dpdk_path: Path to the DPDK submodule directory.

    Returns:
        True if the submodule has a new commit vs what the outer repo tracks.
    """
    diff_result = subprocess.run(
        ["git", "-C", str(dpdk_path), "diff", "--cached", "--stat"],
        capture_output=True,
        text=True,
    )
    log_result = subprocess.run(
        ["git", "-C", str(dpdk_path), "log", "-1", "--oneline"],
        capture_output=True,
        text=True,
    )

    has_staged = bool(diff_result.stdout.strip())
    has_commit = bool(log_result.stdout.strip())

    if not has_staged and not has_commit:
        logger.warning("No submodule change detected in %s", dpdk_path)
        return False

    # Check if submodule pointer changed in the outer repo
    outer_diff = subprocess.run(
        ["git", "diff", "--submodule=short", "--", str(dpdk_path)],
        capture_output=True,
        text=True,
    )
    has_change = bool(outer_diff.stdout.strip())
    if not has_change:
        logger.warning("No submodule pointer change detected in %s", dpdk_path)
    return has_change
