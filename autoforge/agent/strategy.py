"""Context formatting and change validation for the optimization loop."""

from __future__ import annotations

import logging
import subprocess
from collections import Counter
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
    metric_comparison,
    metric_comparison_window,
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
    ]

    cmp_mode = metric_comparison(campaign)
    if cmp_mode != "peak":
        window = metric_comparison_window(campaign)
        lines.append(f"Comparison: {cmp_mode} (window={window})")

    lines.append("")

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


def format_failure_patterns(requests_dir: Path, limit: int = 20) -> str:
    """Scan recent failed requests and summarize failure patterns.

    Args:
        requests_dir: Directory containing request JSON files.
        limit: Maximum number of recent requests to scan.

    Returns:
        A summary string, or empty string if no failures found.
    """
    from autoforge.protocol import TestRequest

    if not requests_dir.is_dir():
        return ""

    json_files = sorted(requests_dir.glob("*.json"), reverse=True)[:limit]
    phase_counts: Counter[str] = Counter()
    error_patterns: dict[str, Counter[str]] = {}

    for path in json_files:
        try:
            request = TestRequest.read(path)
        except (ValueError, KeyError, TypeError, OSError):
            continue

        if request.status != "failed":
            continue

        phase = request.failed_phase or "unknown"
        phase_counts[phase] += 1

        if phase not in error_patterns:
            error_patterns[phase] = Counter()

        error_msg = request.error or ""
        log = _pick_log_for_phase(request, phase)
        pattern = _classify_error(error_msg, log)
        if pattern:
            error_patterns[phase][pattern] += 1

    if not phase_counts:
        return ""

    parts: list[str] = []
    for phase, count in phase_counts.most_common():
        detail_parts: list[str] = []
        for pattern, pcount in error_patterns.get(phase, Counter()).most_common(3):
            detail_parts.append(f"{pcount} {pattern}")
        detail = f" ({', '.join(detail_parts)})" if detail_parts else ""
        parts.append(f"{count} {phase}{detail}")

    return f"Recent failures: {', '.join(parts)}"


def _pick_log_for_phase(request: TestRequest, phase: str) -> str:
    """Return the log snippet matching the phase."""
    if phase == "build":
        return request.build_log_snippet or ""
    if phase == "deploy":
        return getattr(request, "deploy_log_snippet", "") or ""
    if phase == "test":
        return getattr(request, "test_log_snippet", "") or ""
    return ""


def _classify_error(error_msg: str, log: str) -> str:
    """Classify an error into a short pattern label."""
    combined = f"{error_msg}\n{log}".lower()
    if "timeout" in combined:
        return "timeout"
    if "linker" in combined or "undefined reference" in combined or "ld returned" in combined:
        return "linker"
    if "assertion" in combined or "assert" in combined:
        return "assertion"
    if "oom" in combined or "out of memory" in combined or "cannot allocate" in combined:
        return "oom"
    if "permission" in combined or "denied" in combined:
        return "permission"
    if "syntax error" in combined or "parse error" in combined:
        return "syntax"
    if "not found" in combined or "no such file" in combined:
        return "missing-file"
    if error_msg:
        # Use first few words of error as fallback label
        words = error_msg.split()[:4]
        return " ".join(words).rstrip(":")
    return "unknown"


def has_submodule_change(source_path: Path) -> bool:
    """Check whether the source submodule pointer differs from the outer repo.

    Checks both unstaged and staged (cached) changes so that a submodule
    pointer already added via ``git add`` is still detected.

    Args:
        source_path: Path to the source submodule directory.

    Returns:
        True if the outer repo's submodule pointer has changed (i.e. the
        submodule has a different commit than what is currently tracked).

    Raises:
        subprocess.CalledProcessError: If a git diff command fails.
    """
    for extra_args in ([], ["--cached"]):
        cmd = ["git", "diff", *extra_args, "--submodule=short", "--", str(source_path)]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            raise subprocess.CalledProcessError(
                result.returncode, result.args, result.stdout, result.stderr
            )
        if result.stdout.strip():
            return True

    logger.warning("No submodule pointer change detected in %s", source_path)
    return False


def check_scope_compliance(source_path: Path, scope: list[str]) -> list[str]:
    """Return submodule-relative paths that fall outside the configured scope.

    Args:
        source_path: Path to the source submodule directory.
        scope: List of allowed path prefixes (e.g. ``["drivers/net/memif/"]``).

    Returns:
        List of changed file paths not matching any scope prefix. Empty list
        means all changes are in scope (or scope is empty / no files changed).
    """
    if not scope:
        return []

    normalized = [s.rstrip("/") + "/" for s in scope]

    # git diff HEAD covers both staged and unstaged changes vs HEAD
    result = subprocess.run(
        ["git", "diff", "--name-only", "HEAD"],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=str(source_path),
    )
    if result.returncode != 0:
        logger.warning("scope check git diff failed: %s", result.stderr.strip())
        return []

    out_of_scope: list[str] = []
    for line in result.stdout.strip().splitlines():
        path = line.strip()
        if not path:
            continue
        if not any(path.startswith(prefix) for prefix in normalized):
            out_of_scope.append(path)

    return out_of_scope
