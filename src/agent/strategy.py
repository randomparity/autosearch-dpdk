"""Context formatting and change validation for the optimization loop."""

from __future__ import annotations

import json
import logging
import subprocess
from typing import TYPE_CHECKING

from src.agent.hints import resolve_arch

if TYPE_CHECKING:
    from pathlib import Path

    from src.agent.campaign import CampaignConfig

logger = logging.getLogger(__name__)


def format_context(
    history: list[dict],
    campaign: CampaignConfig,
    *,
    profile_summary: dict | None = None,
) -> str:
    """Build a prompt-friendly summary of campaign state and history.

    Args:
        history: List of row dicts from load_history().
        campaign: Parsed campaign.toml as a dict.
        profile_summary: Optional profiling data from the latest run.

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
        selector = min if metric_cfg.get("direction") == "minimize" else max
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

    arch = resolve_arch(campaign)
    if arch:
        lines.append("")
        lines.append(f"Tip: run `uv run autosearch hints` for {arch} optimization guidance.")

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


def format_profile_lines(summary: dict) -> list[str]:
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


def extract_profile_summary(result: object) -> dict | None:
    """Extract profiling summary from a completed test result.

    Args:
        result: TestRequest with results_json field.

    Returns:
        Profile summary dict, or None if not available.
    """
    raw = getattr(result, "results_json", None)
    if raw is None:
        return None
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return None
    elif isinstance(raw, dict):
        data = raw
    else:
        return None
    return data.get("profiling")


def validate_change(dpdk_path: Path) -> bool:
    """Check whether the DPDK submodule pointer differs from the outer repo.

    Args:
        dpdk_path: Path to the DPDK submodule directory.

    Returns:
        True if the outer repo's submodule pointer has changed (i.e. the
        submodule has a different commit than what is currently tracked).
    """
    outer_diff = subprocess.run(
        ["git", "diff", "--submodule=short", "--", str(dpdk_path)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    has_change = bool(outer_diff.stdout.strip())
    if not has_change:
        logger.warning("No submodule pointer change detected in %s", dpdk_path)
    return has_change
