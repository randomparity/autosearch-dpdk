"""Sprint summary generation from experiment results."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from autoforge.agent.history import load_failures, load_history
from autoforge.agent.protocol import find_request_by_seq
from autoforge.agent.sprint import failures_path, requests_dir, results_path
from autoforge.campaign import REPO_ROOT

if TYPE_CHECKING:
    from autoforge.campaign import CampaignConfig

logger = logging.getLogger(__name__)


DEFAULT_TEMPLATE = """\
# Sprint Results: {sprint_name}

{goal_description}

## Overview

| Metric | Value |
|--------|-------|
| Sprint | {sprint_name} |
| Platform | {platform} |
| Metric | {metric_direction} {metric_name} |
| Baseline | {baseline_metric} |
| Final best | {best_metric} (request {best_sequence}) |
| Total gain | {total_gain} ({gain_pct}) |
| Iterations | {iterations_used} used / {iterations_budget} budget |

## Throughput Over Time

![Throughput optimization curve](throughput.jpg)

## Accepted Patches

{accepted_patches_table}

## Rejected Experiments

Experiments that regressed or showed no improvement and were reverted.

{rejected_experiments_table}

## Build/Test Failures

{build_failures_table}

---

## Appendix A: Detailed Patch Discussion

<!-- For each accepted patch, describe: -->
<!-- **What changed.** The specific code modifications. -->
<!-- **Motivation.** Why this optimization was expected to help. -->
<!-- **Why it worked.** The architectural explanation for the improvement. -->

{patch_discussion_prompt}

---

## Appendix B: Architecture Insights

<!-- Describe key architectural properties that drove optimization decisions. -->
<!-- Include a comparison table of relevant hardware parameters. -->
<!-- List lessons learned about what works and what doesn't on this platform. -->

{architecture_insights_prompt}

---

## Appendix C: Tooling Observations

<!-- Document what worked well in the optimization workflow. -->
<!-- List issues encountered and workarounds applied. -->
<!-- Suggest improvements for future sprints. -->

{tooling_observations_prompt}
"""


def generate_summary(campaign: CampaignConfig) -> str:
    """Generate a sprint summary from results data.

    Args:
        campaign: Parsed campaign configuration.

    Returns:
        Rendered markdown summary string.
    """
    data = _load_summary_data(campaign)
    template = _load_template(campaign)
    return _render(template, data)


def _load_summary_data(campaign: CampaignConfig) -> dict[str, Any]:
    """Aggregate sprint data into a dict for template rendering."""
    from autoforge.agent.sprint import active_sprint_name

    res_path = results_path(campaign)
    fail_path = failures_path(campaign)
    req_dir = requests_dir(campaign)

    history = load_history(res_path)
    failures = load_failures(fail_path)

    campaign_cfg = campaign.get("campaign", {})
    metric_cfg = campaign.get("metric", {})
    goal_cfg = campaign.get("goal", {})
    platform_cfg = campaign.get("platform", {})

    try:
        sprint_name = active_sprint_name(campaign)
    except (KeyError, FileNotFoundError):
        sprint_name = campaign_cfg.get("name", "unknown")

    direction = metric_cfg.get("direction", "maximize")
    metric_name = metric_cfg.get("name", "metric")

    # Extract scored rows
    scored = _scored_rows(history, direction)

    # Baseline: first completed result
    baseline = _first_completed(history)
    baseline_metric = f"{baseline['value']:.2f}" if baseline else "N/A"
    baseline_seq = baseline["sequence"] if baseline else "?"

    # Best result
    best = scored[0] if scored else None
    best_metric = f"{best['value']:.2f}" if best else "N/A"
    best_seq = best["sequence"] if best else "?"

    # Gain calculation
    if baseline and best:
        gain = best["value"] - baseline["value"]
        gain_pct = (gain / baseline["value"]) * 100 if baseline["value"] else 0
        total_gain = f"{gain:+.2f} {metric_name}"
        gain_pct_str = f"{gain_pct:+.1f}%"
    else:
        total_gain = "N/A"
        gain_pct_str = "N/A"

    # Build tables
    accepted_table = _build_accepted_table(history, baseline, direction)
    rejected_table = _build_rejected_table(failures)
    failures_table = _build_failures_table(history, req_dir)

    # Tag summary
    tags_summary = _build_tags_summary(history)

    # Patch discussion prompts
    patch_prompts = _build_patch_prompts(history, baseline, direction)

    return {
        "sprint_name": sprint_name,
        "goal_description": goal_cfg.get("description", "").strip(),
        "platform": platform_cfg.get("arch", "unknown"),
        "metric_name": metric_name,
        "metric_direction": direction,
        "baseline_metric": f"{baseline_metric} (request {baseline_seq})",
        "best_metric": best_metric,
        "best_sequence": best_seq,
        "total_gain": total_gain,
        "gain_pct": gain_pct_str,
        "iterations_used": len(history),
        "iterations_budget": campaign_cfg.get("max_iterations", "?"),
        "accepted_patches_table": accepted_table,
        "rejected_experiments_table": rejected_table,
        "build_failures_table": failures_table,
        "tags_summary": tags_summary,
        "patch_discussion_prompt": patch_prompts,
        "architecture_insights_prompt": ("<!-- Add architecture-specific insights here. -->"),
        "tooling_observations_prompt": ("<!-- Add tooling observations here. -->"),
    }


def _scored_rows(
    history: list[dict],
    direction: str,
) -> list[dict[str, Any]]:
    """Extract rows with valid metrics, sorted by best first."""
    rows = []
    for row in history:
        val = row.get("metric_value", "")
        if val:
            try:
                rows.append(
                    {
                        "sequence": row.get("sequence", "?"),
                        "value": float(val),
                        "status": row.get("status", "?"),
                        "description": row.get("description", ""),
                        "tags": row.get("tags", ""),
                    }
                )
            except ValueError:
                continue
    reverse = direction != "minimize"
    rows.sort(key=lambda r: r["value"], reverse=reverse)
    return rows


def _first_completed(history: list[dict]) -> dict[str, Any] | None:
    """Find the first completed result (baseline)."""
    for row in history:
        if row.get("status") == "completed" and row.get("metric_value"):
            try:
                return {
                    "sequence": row.get("sequence", "?"),
                    "value": float(row["metric_value"]),
                }
            except ValueError:
                continue
    return None


def _build_accepted_table(
    history: list[dict],
    baseline: dict | None,
    direction: str,
) -> str:
    """Build markdown table of accepted (improving) results."""
    if not baseline:
        return "No accepted patches."

    base_val = baseline["value"]
    lines = [
        "| # | Request | Metric | Cumulative gain | Description |",
        "|---|---------|--------|-----------------|-------------|",
    ]

    patch_num = 0
    running_best = base_val
    compare = max if direction == "maximize" else min
    for row in history:
        val_str = row.get("metric_value", "")
        if not val_str or row.get("status") != "completed":
            continue
        try:
            val = float(val_str)
        except ValueError:
            continue

        if compare(val, running_best) == val and val != running_best:
            patch_num += 1
            running_best = val
            gain_pct = ((val - base_val) / base_val * 100) if base_val else 0
            seq = row.get("sequence", "?")
            desc = row.get("description", "")
            lines.append(f"| {patch_num} | {seq} | {val:.2f} | {gain_pct:+.1f}% | {desc} |")

    if patch_num == 0:
        return "No improvements over baseline."
    return "\n".join(lines)


def _build_rejected_table(failures: list[dict]) -> str:
    """Build markdown table of rejected experiments."""
    if not failures:
        return "No rejected experiments."

    lines = [
        "| Metric | Description | Diff |",
        "|--------|-------------|------|",
    ]
    for row in failures:
        metric = row.get("metric_value", "N/A") or "N/A"
        desc = row.get("description", "?")
        diff = row.get("diff_summary", "").replace("\n", " ")
        lines.append(f"| {metric} | {desc} | {diff} |")
    return "\n".join(lines)


def _build_failures_table(history: list[dict], req_dir: Path) -> str:
    """Build markdown table of build/test failures."""
    failed = [r for r in history if r.get("status") == "failed"]
    if not failed:
        return "No build/test failures."

    lines = [
        "| Request | Description | Error |",
        "|---------|-------------|-------|",
    ]
    for row in failed:
        seq = row.get("sequence", "?")
        desc = row.get("description", "?")
        error = ""
        try:
            req = find_request_by_seq(int(seq), req_dir)
            if req and req.error:
                error = req.error[:80]
        except (ValueError, TypeError):
            pass
        lines.append(f"| {seq} | {desc} | {error} |")
    return "\n".join(lines)


def _build_tags_summary(history: list[dict]) -> str:
    """Build a summary of experiment tags."""
    tag_counts: dict[str, int] = {}
    for row in history:
        tags_str = row.get("tags", "")
        if tags_str:
            for tag in tags_str.split(","):
                tag = tag.strip()
                if tag:
                    tag_counts[tag] = tag_counts.get(tag, 0) + 1
    if not tag_counts:
        return ""
    parts = [f"{tag}: {count}" for tag, count in sorted(tag_counts.items())]
    return "Tags: " + ", ".join(parts)


def _build_patch_prompts(
    history: list[dict],
    baseline: dict | None,
    direction: str,
) -> str:
    """Generate placeholder prompts for each accepted patch."""
    if not baseline:
        return "<!-- No accepted patches to discuss. -->"

    base_val = baseline["value"]
    prompts = []
    running_best = base_val
    compare = max if direction == "maximize" else min
    patch_num = 0
    for row in history:
        val_str = row.get("metric_value", "")
        if not val_str or row.get("status") != "completed":
            continue
        try:
            val = float(val_str)
        except ValueError:
            continue
        if compare(val, running_best) == val and val != running_best:
            patch_num += 1
            running_best = val
            desc = row.get("description", "?")
            prompts.append(
                f"### Patch {patch_num}: {desc} (request {row.get('sequence', '?')})\n\n"
                f"**What changed.** <!-- Describe the specific code modifications. -->\n\n"
                f"**Motivation.** <!-- Why was this expected to help? -->\n\n"
                f"**Why it worked.** <!-- Architectural explanation for the "
                f"{val - base_val:+.2f} improvement. -->"
            )

    if not prompts:
        return "<!-- No accepted patches to discuss. -->"
    return "\n\n".join(prompts)


def _load_template(campaign: CampaignConfig) -> str:
    """Load summary template, checking project directory first."""
    project = campaign.get("project", {}).get("name", "")
    if project:
        project_template = REPO_ROOT / "projects" / project / "summary-template.md"
        if project_template.exists():
            return project_template.read_text()
    return DEFAULT_TEMPLATE


def _render(template: str, data: dict[str, Any]) -> str:
    """Render a template with data using str.format_map."""
    try:
        return template.format_map(data)
    except KeyError as exc:
        logger.warning("Missing template key: %s", exc)
        # Fall back to partial rendering with a safe dict
        safe = _SafeDict(data)
        return template.format_map(safe)


class _SafeDict(dict):
    """Dict that returns placeholder for missing keys."""

    def __missing__(self, key: str) -> str:
        return f"{{{{ {key} }}}}"
