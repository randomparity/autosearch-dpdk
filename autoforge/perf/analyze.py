"""Structured analysis of folded stacks and hardware counters."""

from __future__ import annotations

import logging
from collections import Counter
from typing import Any, TypedDict

from autoforge.perf.arch import ArchProfile


class ProfileSummary(TypedDict):
    """Return type of summarize()."""

    top_functions: list[dict[str, Any]]
    derived_metrics: dict[str, float]
    diagnostics: list[dict[str, Any]]
    total_samples: int


logger = logging.getLogger(__name__)


def top_functions(stacks: dict[str, int], limit: int = 20) -> list[dict[str, Any]]:
    """Return the hottest functions by sample count.

    Credits the leaf (last) frame of each stack trace.

    Args:
        stacks: Folded-stack dict from fold_stacks().
        limit: Maximum number of results.

    Returns:
        List of dicts with 'name', 'samples', and 'pct' keys,
        sorted by samples descending.
    """
    func_counts: Counter[str] = Counter()
    total = 0
    for stack, count in stacks.items():
        frames = stack.split(";")
        leaf = frames[-1]
        func_counts[leaf] += count
        total += count

    if total == 0:
        return []

    return [
        {"name": name, "samples": samples, "pct": round(samples / total * 100, 2)}
        for name, samples in func_counts.most_common(limit)
    ]


def hot_paths(
    stacks: dict[str, int],
    depth: int = 5,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Return the top N full stack paths by sample count.

    Args:
        stacks: Folded-stack dict from fold_stacks().
        depth: Maximum number of frames to include per path.
        limit: Maximum number of results.

    Returns:
        List of dicts with 'path', 'samples', and 'pct' keys.
    """
    total = sum(stacks.values())
    if total == 0:
        return []

    # Truncate stacks to depth and re-aggregate
    truncated: Counter[str] = Counter()
    for stack, count in stacks.items():
        frames = stack.split(";")
        key = ";".join(frames[-depth:]) if len(frames) > depth else stack
        truncated[key] += count

    return [
        {"path": path, "samples": samples, "pct": round(samples / total * 100, 2)}
        for path, samples in truncated.most_common(limit)
    ]


def compute_derived_metrics(
    counters: dict[str, float],
    arch_profile: ArchProfile,
) -> dict[str, float]:
    """Calculate derived metrics (IPC, miss rates) from raw counters.

    Uses the formulas in arch_profile['derived_metrics']. Each formula
    is a simple 'a / b' expression referencing abstract event names.

    Args:
        counters: Raw counter values keyed by abstract event name.
        arch_profile: Loaded arch profile dict.

    Returns:
        Dict of derived metric name to computed value.
    """
    events = arch_profile.get("events", {})
    abstract_values: dict[str, float] = {}
    for abstract_name, pmu_event in events.items():
        if pmu_event in counters:
            abstract_values[abstract_name] = counters[pmu_event]
        elif abstract_name in counters:
            abstract_values[abstract_name] = counters[abstract_name]

    derived = {}
    for metric_name, formula in arch_profile.get("derived_metrics", {}).items():
        parts = formula.split("/")
        if len(parts) != 2:
            logger.warning(
                "Skipping malformed derived metric formula: %s = %s",
                metric_name,
                formula,
            )
            continue
        numerator_key = parts[0].strip()
        denominator_key = parts[1].strip()
        num = abstract_values.get(numerator_key)
        den = abstract_values.get(denominator_key)
        if num is not None and den is not None and den > 0:
            derived[metric_name] = round(num / den, 6)

    return derived


def diagnose(
    counters: dict[str, float],
    stacks: dict[str, int],
    arch_profile: ArchProfile,
) -> list[dict[str, Any]]:
    """Evaluate arch heuristics against profiling data.

    Args:
        counters: Raw counter values.
        stacks: Folded-stack dict.
        arch_profile: Loaded arch profile with heuristics.

    Returns:
        List of triggered diagnostics, each a dict with 'priority',
        'category', 'evidence', and 'suggestions' keys.
    """
    derived = compute_derived_metrics(counters, arch_profile)
    top = top_functions(stacks, limit=5)
    triggered = []

    for i, heuristic in enumerate(arch_profile.get("heuristics", [])):
        condition = heuristic.get("condition", "")
        if _evaluate_condition(condition, derived):
            evidence_parts = [condition]
            if top:
                evidence_parts.append(f"top function: {top[0]['name']} ({top[0]['pct']}%)")
            triggered.append(
                {
                    "priority": i + 1,
                    "category": _category_from_condition(condition),
                    "evidence": ", ".join(evidence_parts),
                    "suggestions": heuristic.get("suggestions", []),
                }
            )

    return triggered


def _evaluate_condition(condition: str, derived: dict[str, float]) -> bool:
    """Evaluate a simple 'metric < threshold' or 'metric > threshold' condition."""
    for op in ("<", ">"):
        if op in condition:
            parts = condition.split(op, 1)
            metric_name = parts[0].strip()
            try:
                threshold = float(parts[1].strip())
            except ValueError:
                return False
            value = derived.get(metric_name)
            if value is None:
                return False
            if op == "<":
                return value < threshold
            return value > threshold
    return False


def _category_from_condition(condition: str) -> str:
    """Derive a category name from a heuristic condition string."""
    metric = condition.split("<")[0].split(">")[0].strip()
    category_map = {
        "ipc": "pipeline_utilization",
        "l1d_miss_rate": "cache_pressure",
        "llc_miss_rate": "cache_pressure",
        "l3_miss_rate": "cache_pressure",
        "l2_miss_rate": "cache_pressure",
        "backend_bound": "backend_stall",
        "frontend_bound": "frontend_stall",
        "branch_miss_rate": "branch_prediction",
    }
    return category_map.get(metric, metric)


def summarize(
    counters: dict[str, float],
    stacks: dict[str, int],
    arch_profile: ArchProfile,
) -> ProfileSummary:
    """Produce a compact JSON-serializable summary for results_json.

    Args:
        counters: Raw counter values.
        stacks: Folded-stack dict.
        arch_profile: Loaded arch profile.

    Returns:
        Dict with 'top_functions', 'derived_metrics', 'diagnostics',
        and 'total_samples' keys.
    """
    return {
        "top_functions": top_functions(stacks, limit=10),
        "derived_metrics": compute_derived_metrics(counters, arch_profile),
        "diagnostics": diagnose(counters, stacks, arch_profile),
        "total_samples": sum(stacks.values()),
    }
