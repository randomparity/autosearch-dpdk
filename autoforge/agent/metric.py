"""Metric comparison for optimization results."""

from __future__ import annotations

from autoforge.campaign import CampaignConfig, metric_threshold
from autoforge.protocol import Direction


def compare_metric(current: float, best: float, direction: Direction) -> bool:
    """Return True if current is strictly better than best.

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
    threshold = metric_threshold(campaign)
    if not threshold or metric is None or best_val is None:
        return False
    return abs(metric - best_val) < threshold
