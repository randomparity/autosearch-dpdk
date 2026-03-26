"""Differential comparison of two profiling runs."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any, TypedDict


class StackDiff(TypedDict):
    """Return type of diff_stacks()."""

    baseline_total_samples: int
    current_total_samples: int
    significant_changes: list[dict[str, Any]]
    net_assessment: str


class CounterDiff(TypedDict):
    """Return type of diff_counters()."""

    deltas: dict[str, dict[str, Any]]


def load_folded(path: Path) -> dict[str, int]:
    """Parse a folded-stack file into {stack: count}.

    Args:
        path: Path to a file in Brendan Gregg folded-stack format.

    Returns:
        Dict mapping stack strings to sample counts.
    """
    stacks: dict[str, int] = {}
    try:
        text = path.read_text()
    except OSError as exc:
        msg = f"Cannot load folded stacks: {path}: {exc}"
        raise FileNotFoundError(msg) from exc
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        # Format: "frame1;frame2;frame3 count"
        parts = line.rsplit(None, 1)
        if len(parts) == 2:
            try:
                stacks[parts[0]] = int(parts[1])
            except ValueError:
                continue
    return stacks


def _leaf_pcts(stacks: dict[str, int]) -> dict[str, float]:
    """Compute per-leaf-function percentage from a stack dict."""
    func_counts: Counter[str] = Counter()
    total = 0
    for stack, count in stacks.items():
        frames = stack.split(";")
        leaf = frames[-1]
        func_counts[leaf] += count
        total += count

    if total == 0:
        return {}
    return {name: count / total * 100 for name, count in func_counts.items()}


def diff_stacks(
    baseline: dict[str, int],
    current: dict[str, int],
    threshold: float = 1.0,
) -> StackDiff:
    """Compare two folded-stack profiles.

    Args:
        baseline: Baseline profile stacks.
        current: Current (modified) profile stacks.
        threshold: Minimum delta (percentage points) to report.

    Returns:
        Dict with 'significant_changes' list and 'net_assessment'.
    """
    base_pcts = _leaf_pcts(baseline)
    curr_pcts = _leaf_pcts(current)
    all_symbols = set(base_pcts) | set(curr_pcts)

    changes = []
    for symbol in all_symbols:
        base_pct = base_pcts.get(symbol, 0.0)
        curr_pct = curr_pcts.get(symbol, 0.0)
        delta = curr_pct - base_pct
        if abs(delta) >= threshold:
            verdict = "regressed" if delta > 0 else "improved" if delta < 0 else "neutral"
            changes.append(
                {
                    "symbol": symbol,
                    "baseline_pct": round(base_pct, 2),
                    "current_pct": round(curr_pct, 2),
                    "delta_pct": round(delta, 2),
                    "verdict": verdict,
                }
            )

    changes.sort(key=lambda c: abs(c["delta_pct"]), reverse=True)

    # Assess based on the direction of the largest absolute change.
    # Percentage redistribution always nets to ~0, so summing deltas
    # is misleading. Instead, the biggest mover determines the verdict.
    if changes:
        biggest = changes[0]
        assessment = biggest["verdict"]
    else:
        assessment = "neutral"

    return {
        "baseline_total_samples": sum(baseline.values()),
        "current_total_samples": sum(current.values()),
        "significant_changes": changes,
        "net_assessment": assessment,
    }


def diff_counters(
    baseline: dict[str, float],
    current: dict[str, float],
) -> CounterDiff:
    """Compare two counter sets.

    Args:
        baseline: Baseline counter values.
        current: Current counter values.

    Returns:
        Dict with 'deltas' mapping event names to change details.
    """
    all_events = set(baseline) | set(current)
    deltas = {}
    for event in sorted(all_events):
        base_val = baseline.get(event, 0.0)
        curr_val = current.get(event, 0.0)
        if base_val:
            change_pct = (curr_val - base_val) / base_val * 100
        elif curr_val:
            change_pct = float("inf")
        else:
            change_pct = 0.0
        deltas[event] = {
            "baseline": base_val,
            "current": curr_val,
            "change_pct": round(change_pct, 2),
        }
    return {"deltas": deltas}
