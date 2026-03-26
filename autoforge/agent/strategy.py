"""Context formatting and change validation for the optimization loop."""

from __future__ import annotations

import logging
import subprocess
from typing import TYPE_CHECKING, Any

from autoforge.agent.hints import workload_hints
from autoforge.campaign import platform_arch

if TYPE_CHECKING:
    from pathlib import Path

    from autoforge.protocol import TestRequest

from autoforge.campaign import (
    CampaignConfig,
    campaign_meta,
    campaign_name,
    goal_description,
    metric_direction,
    metric_name,
    project_config,
)

logger = logging.getLogger(__name__)


def format_context(
    history: list[dict[str, str]],
    campaign: CampaignConfig,
    *,
    profile_summary: dict[str, Any] | None = None,
) -> str:
    """Build a prompt-friendly summary of campaign state and history.

    Args:
        history: List of row dicts from load_history().
        campaign: Parsed campaign.toml as a dict.
        profile_summary: Optional profiling data from the latest run.

    Returns:
        A multi-line string suitable for display or prompt injection.
    """
    proj_cfg = project_config(campaign)
    camp_meta = campaign_meta(campaign)

    goal = goal_description(campaign)

    lines = [
        f"Campaign: {campaign_name(campaign)}",
        f"Objective: {metric_direction(campaign)} {metric_name(campaign)}",
        f"Project scope: {', '.join(proj_cfg.get('scope', []))}",
        f"Iterations: {len(history)} / {camp_meta.get('max_iterations', '?')}",
        "",
    ]

    if goal:
        lines.append(f"Goal: {goal}")
        lines.append("")

    scored = _scored_rows(history)
    if scored:
        selector = min if metric_direction(campaign) == "minimize" else max
        best_val, best_row = selector(scored, key=lambda x: x[0])
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

    if profile_summary:
        lines.append("")
        lines.extend(format_profile_lines(profile_summary))

    arch = platform_arch(campaign)
    if arch and profile_summary:
        wh = workload_hints(arch, profile_summary)
        if wh:
            lines.append("")
            lines.append(wh)

    if arch:
        lines.append("")
        lines.append(f"Tip: run `uv run autoforge hints` for {arch} optimization guidance.")

    return "\n".join(lines)


def _scored_rows(history: list[dict[str, str]]) -> list[tuple[float, dict[str, str]]]:
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


def format_profile_lines(summary: dict[str, Any]) -> list[str]:
    """Format profiling data for prompt context.

    Args:
        summary: Profile summary dict from summarize().

    Returns:
        List of formatted lines.
    """
    lines = ["Profiling data (latest run):"]
    if top := summary.get("top_functions"):
        lines.append("  Hot functions:")
        for f in top[:10]:
            lines.append(f"    {f['pct']:5.1f}%  {f['name']}")
    if derived := summary.get("derived_metrics"):
        metrics_parts = []
        if (ipc := derived.get("ipc")) is not None:
            metrics_parts.append(f"IPC={ipc:.3f}")
        if (l1d := derived.get("l1d_miss_rate")) is not None:
            metrics_parts.append(f"L1d-miss-rate={l1d:.4f}")
        if (bb := derived.get("backend_bound")) is not None:
            metrics_parts.append(f"backend-bound={bb:.3f}")
        if metrics_parts:
            lines.append(f"  Metrics: {', '.join(metrics_parts)}")
    if diags := summary.get("diagnostics"):
        lines.append("  Diagnostics:")
        for d in diags[:5]:
            lines.append(f"    - {d.get('category', '?')}: {d.get('evidence', '')}")
    return lines


def extract_profile_summary(result: TestRequest) -> dict[str, Any] | None:
    """Extract profiling summary from a completed test result.

    Args:
        result: TestRequest with results_json field.

    Returns:
        Profile summary dict, or None if not available.
    """
    raw = result.results_json
    if raw is None:
        return None
    if not isinstance(raw, dict):
        logger.debug("results_json is not a dict, got %s", type(raw).__name__)
        return None
    return raw.get("profiling")


def has_submodule_change(source_path: Path) -> bool:
    """Check whether the source submodule pointer differs from the outer repo.

    Args:
        source_path: Path to the source submodule directory.

    Returns:
        True if the outer repo's submodule pointer has changed (i.e. the
        submodule has a different commit than what is currently tracked).

    Raises:
        subprocess.CalledProcessError: If the git diff command fails.
    """
    outer_diff = subprocess.run(
        ["git", "diff", "--submodule=short", "--", str(source_path)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if outer_diff.returncode != 0:
        raise subprocess.CalledProcessError(
            outer_diff.returncode,
            outer_diff.args,
            outer_diff.stdout,
            outer_diff.stderr,
        )
    has_change = bool(outer_diff.stdout.strip())
    if not has_change:
        logger.warning("No submodule pointer change detected in %s", source_path)
    return has_change
