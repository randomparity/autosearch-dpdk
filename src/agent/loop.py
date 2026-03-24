"""Main autoresearch optimization loop — CLI entry point."""

from __future__ import annotations

import argparse
import logging
import sys
import tomllib
from pathlib import Path

from src.agent.autonomous import (
    _below_threshold,
    _record_result_or_revert,
    extract_profile_summary,
    run_autonomous,
)
from src.agent.campaign import CampaignConfig
from src.agent.git_ops import (
    ensure_optimization_branch,
    git_add_commit_push,
    git_submodule_head,
)
from src.agent.history import append_result, best_result, load_history
from src.agent.protocol import create_request, next_sequence, poll_for_completion
from src.agent.strategy import format_context, format_profile_lines, validate_change
from src.logging_config import setup_logging

logger = logging.getLogger(__name__)


def load_campaign(path: Path) -> CampaignConfig:
    """Load and return the campaign TOML configuration.

    Raises:
        FileNotFoundError: If the config file does not exist.
        tomllib.TOMLDecodeError: If the file is not valid TOML.
    """
    with open(path, "rb") as f:
        return tomllib.load(f)


def run_interactive_iteration(
    campaign: CampaignConfig,
    dpdk_path: Path,
    dry_run: bool,
) -> bool:
    """Run one iteration of the interactive optimization loop.

    Returns True to continue, False to stop.
    """
    history = load_history()
    metric_cfg = campaign["metric"]
    direction = metric_cfg.get("direction", "maximize")
    max_iter = campaign.get("campaign", {}).get("max_iterations", 50)

    if len(history) >= max_iter:
        print(f"Reached max iterations ({max_iter}). Stopping.")
        return False

    print("\n" + "=" * 60)
    print(format_context(history, campaign))
    print("=" * 60)

    print("\nMake your DPDK changes in the submodule, commit them, then press Enter.")
    print("Type 'quit' to stop the loop.")
    user_input = input("> ").strip()
    if user_input.lower() in ("quit", "exit", "q"):
        return False

    if not validate_change(dpdk_path):
        print("No submodule change detected. Skipping iteration.")
        return True

    commit = git_submodule_head(dpdk_path)
    description = input("Describe this change: ").strip() or "No description"
    seq = next_sequence()
    poll_interval = campaign.get("agent", {}).get("poll_interval", 30)
    timeout = campaign.get("agent", {}).get("timeout_minutes", 60) * 60

    request_path = create_request(seq, commit, campaign, description)

    git_add_commit_push(
        [str(request_path), str(dpdk_path)],
        f"iteration {seq:04d}: {description}",
        dry_run=dry_run,
    )
    print(f"Request {seq:04d} submitted. Polling for results...")

    if dry_run:
        print("[dry-run] Skipping poll — no push was made.")
        append_result(seq, commit, None, "dry_run", description)
        return True

    try:
        result = poll_for_completion(seq, timeout=timeout, interval=poll_interval)
    except TimeoutError:
        print(f"Request {seq:04d} timed out.")
        append_result(seq, commit, None, "timed_out", description)
        return True

    if result.status == "failed":
        print(f"Request {seq:04d} FAILED: {result.error}")
        append_result(seq, commit, None, "failed", description)
        return True

    metric = result.metric_value
    print(f"Request {seq:04d} completed. Metric: {metric}")

    # Display profiling summary if available
    profile_summary = extract_profile_summary(result)
    if profile_summary:
        for line in format_profile_lines(profile_summary):
            print(line)

    current_best = best_result(direction=direction)
    best_val = float(current_best["metric_value"]) if current_best is not None else None

    append_result(seq, commit, metric, "completed", description)

    _record_result_or_revert(
        metric,
        best_val,
        direction,
        seq,
        commit,
        description,
        dpdk_path,
        dry_run,
    )

    if _below_threshold(metric, best_val, campaign):
        threshold = campaign["metric"]["threshold"]
        print(f"Improvement below threshold ({threshold}). Stopping early.")
        return False

    return True


def main() -> None:
    """Entry point for the autosearch agent."""
    parser = argparse.ArgumentParser(description="Autosearch DPDK optimization agent")
    parser.add_argument(
        "--campaign",
        default="config/campaign.toml",
        help="Path to campaign TOML config",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Skip git push (local testing)",
    )
    parser.add_argument(
        "--autonomous",
        action="store_true",
        help="Use Claude API for automated change proposals",
    )
    parser.add_argument(
        "--provider",
        choices=["anthropic", "openrouter"],
        default="anthropic",
        help="API provider for autonomous mode (default: anthropic)",
    )
    parser.add_argument(
        "--log-level",
        choices=["debug", "info", "warning", "error"],
        default=None,
        help="Log level (default: info, or LOG_LEVEL env var)",
    )
    parser.add_argument(
        "--log-file",
        default=None,
        help="Path to log file (logs to stdout and file)",
    )
    args = parser.parse_args()

    setup_logging(args.log_level, args.log_file)

    campaign = load_campaign(Path(args.campaign))
    dpdk_path = Path(campaign.get("dpdk", {}).get("submodule_path", "dpdk"))
    opt_branch = campaign.get("dpdk", {}).get("optimization_branch", "autosearch/optimize")
    ensure_optimization_branch(dpdk_path, opt_branch)

    if args.autonomous:
        try:
            run_autonomous(campaign, dpdk_path, args.dry_run, args.provider)
        except (ImportError, ValueError) as exc:
            print(f"Error: {exc}")
            sys.exit(1)
    else:
        while run_interactive_iteration(campaign, dpdk_path, args.dry_run):
            pass

    print("Optimization loop finished.")


if __name__ == "__main__":
    main()
