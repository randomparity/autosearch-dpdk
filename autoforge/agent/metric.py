"""Metric comparison for optimization results."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from autoforge.campaign import CampaignConfig

Direction = Literal["maximize", "minimize"]


def compare_metric(current: float, best: float, direction: Direction) -> bool:
    """Return True if current is strictly better than best.

    Args:
        current: The metric value from the latest iteration.
        best: The best metric value seen so far.
        direction: Either 'maximize' or 'minimize'.

    Raises:
        ValueError: If direction is not 'maximize' or 'minimize'.
    """
    if direction == "maximize":
        return current > best
    if direction == "minimize":
        return current < best
    msg = f"Unknown direction {direction!r}, must be 'maximize' or 'minimize'"
    raise ValueError(msg)


def below_threshold(
    metric: float | None,
    best_val: float | None,
    campaign: CampaignConfig,
) -> bool:
    """Check if improvement between metric and best_val is below threshold."""
    threshold = campaign.get("metric", {}).get("threshold")
    if threshold is None or metric is None or best_val is None:
        return False
    return abs(metric - best_val) < threshold
