"""Autonomous optimization loop using the Claude API."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from src.agent.campaign import CampaignConfig
from src.agent.git_ops import (
    get_diff_summary,
    git_add_commit_push,
    git_submodule_head,
    revert_last_change,
)
from src.agent.history import (
    append_failure,
    append_result,
    best_result,
    format_failures,
    load_failures,
    load_history,
)
from src.agent.metric import compare_metric
from src.agent.protocol import create_request, next_sequence, poll_for_completion
from src.agent.strategy import format_context, validate_change

logger = logging.getLogger(__name__)


def create_api_client(provider: str) -> tuple[object, str]:
    """Build an Anthropic-compatible API client and model ID.

    Args:
        provider: "anthropic" or "openrouter".

    Returns:
        (client, model_id) tuple.

    Raises:
        ImportError: If the anthropic package is not installed.
        ValueError: If required environment variables are missing.
    """
    try:
        import anthropic
    except ImportError:
        msg = "'anthropic' package required for autonomous mode. Install with: uv add anthropic"
        raise ImportError(msg) from None

    if provider == "openrouter":
        api_key = os.environ.get("OPENROUTER_API_KEY")
        if not api_key:
            msg = "OPENROUTER_API_KEY environment variable required"
            raise ValueError(msg)
        client = anthropic.Anthropic(
            base_url="https://openrouter.ai/api",
            api_key=api_key,
        )
        model = "anthropic/claude-opus-4-6"
    else:
        client = anthropic.Anthropic()
        model = "claude-opus-4-6"

    return client, model


def _below_threshold(
    metric: float | None,
    best_val: float | None,
    campaign: CampaignConfig,
) -> bool:
    """Check if improvement between metric and best_val is below threshold."""
    threshold = campaign.get("metric", {}).get("threshold")
    if threshold is None or metric is None or best_val is None:
        return False
    return abs(metric - best_val) < threshold


def _record_result_or_revert(
    metric: float | None,
    best_val: float | None,
    direction: str,
    seq: int,
    commit: str,
    description: str,
    dpdk_path: Path,
    dry_run: bool,
) -> bool:
    """Record a successful result or revert the change and record a failure.

    Returns True if the result was an improvement.
    """
    improved = best_val is None or (
        metric is not None and compare_metric(metric, best_val, direction)
    )

    if improved:
        print(
            f"Improvement! {best_val} -> {metric}"
            if best_val is not None
            else f"Baseline: {metric}"
        )
        files = ["results.tsv", str(dpdk_path)]
        git_add_commit_push(files, f"results: iteration {seq:04d}", dry_run=dry_run)
    else:
        print(f"No improvement ({metric} vs best {best_val}). Reverting.")
        diff_summary = get_diff_summary(dpdk_path)
        revert_last_change(dpdk_path)
        append_failure(commit, metric, description, diff_summary)
        files = ["results.tsv", "failures.tsv", str(dpdk_path)]
        git_add_commit_push(files, f"revert: iteration {seq:04d}", dry_run=dry_run)

    return improved


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


def run_autonomous(
    campaign: CampaignConfig,
    dpdk_path: Path,
    dry_run: bool,
    provider: str = "anthropic",
) -> None:
    """Run the autonomous optimization loop using the Claude API.

    Args:
        campaign: Parsed campaign configuration.
        dpdk_path: Path to the DPDK submodule.
        dry_run: If True, skip git push operations.
        provider: API provider ("anthropic" or "openrouter").
    """
    client, model = create_api_client(provider)
    max_iter = campaign.get("campaign", {}).get("max_iterations", 50)

    last_profile_summary = None
    for _ in range(max_iter):
        history = load_history()
        failures = load_failures()
        context = format_context(
            history,
            campaign,
            profile_summary=last_profile_summary,
        )

        goal = campaign.get("goal", {}).get("description", "").strip()
        goal_block = f"\nGoal:\n{goal}\n" if goal else ""

        failures_block = format_failures(failures)
        failures_section = f"\n{failures_block}\n" if failures_block else ""

        prompt = (
            f"You are optimizing DPDK for maximum throughput.\n"
            f"{goal_block}\n"
            f"Current state:\n{context}\n"
            f"{failures_section}\n"
            f"Propose a specific code change to the DPDK source "
            f"in {dpdk_path}. "
            f"Focus on the scoped areas. Describe the change and "
            f"the file(s) to modify."
        )

        response = client.messages.create(
            model=model,
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
        )
        proposal = response.content[0].text
        print(f"\nClaude proposes:\n{proposal}\n")

        user_input = input("Apply this change? [y/N/quit]: ").strip().lower()
        if user_input == "quit":
            break
        if user_input != "y":
            continue

        if not validate_change(dpdk_path):
            print("No submodule change detected after proposal. Skipping.")
            continue

        commit = git_submodule_head(dpdk_path)
        seq = next_sequence()
        description = proposal[:200]
        poll_interval = campaign.get("agent", {}).get("poll_interval", 30)
        timeout = campaign.get("agent", {}).get("timeout_minutes", 60) * 60

        request_path = create_request(seq, commit, campaign, description)
        git_add_commit_push(
            [str(request_path), str(dpdk_path)],
            f"auto iteration {seq:04d}",
            dry_run=dry_run,
        )

        if dry_run:
            append_result(seq, commit, None, "dry_run", description)
            continue

        try:
            result = poll_for_completion(seq, timeout=timeout, interval=poll_interval)
        except TimeoutError:
            append_result(seq, commit, None, "timed_out", description)
            continue

        metric = result.metric_value if result.status == "completed" else None

        # Extract profiling data for next iteration's context
        last_profile_summary = extract_profile_summary(result)
        direction = campaign.get("metric", {}).get("direction", "maximize")
        prev_best = best_result(direction=direction)
        prev_val = float(prev_best["metric_value"]) if prev_best is not None else None

        append_result(seq, commit, metric, result.status, description)

        _record_result_or_revert(
            metric,
            prev_val,
            direction,
            seq,
            commit,
            description,
            dpdk_path,
            dry_run,
        )

        if _below_threshold(metric, prev_val, campaign):
            print("Improvement below threshold. Stopping early.")
            break
