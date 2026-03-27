"""Interactive optimization loop — manual fallback CLI entry point."""

from __future__ import annotations

import argparse
from pathlib import Path

from autoforge.agent.git_ops import (
    ResultContext,
    ensure_optimization_branch,
    git_add_commit_push,
    git_submodule_head,
)
from autoforge.agent.history import append_result, best_result, load_history
from autoforge.agent.judge import apply_judge_verdict
from autoforge.agent.metric import below_threshold
from autoforge.agent.protocol import create_request, next_sequence, poll_for_completion
from autoforge.agent.sprint import failures_path, requests_dir, results_path
from autoforge.agent.strategy import (
    extract_profile_summary,
    format_context,
    format_profile_lines,
    has_submodule_change,
)
from autoforge.campaign import (
    CampaignConfig,
    agent_poll_interval,
    agent_timeout,
    campaign_max_iterations,
    load_campaign,
    metric_direction,
    metric_threshold,
    optimization_branch,
    project_name,
    resolve_campaign_path,
    submodule_path,
)
from autoforge.logging_config import setup_logging
from autoforge.protocol import Direction


def run_interactive_iteration(
    campaign: CampaignConfig,
    source_path: Path,
    dry_run: bool,
) -> bool:
    """Run one iteration of the interactive optimization loop.

    Returns True to continue, False to stop.
    """
    req = requests_dir()
    res = results_path()
    fail = failures_path()

    history = load_history(res)
    direction: Direction = metric_direction(campaign)
    max_iter = campaign_max_iterations(campaign)

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

    if not has_submodule_change(source_path):
        print("No submodule change detected. Skipping iteration.")
        return True

    commit = git_submodule_head(source_path)
    description = input("Describe this change: ").strip() or "No description"
    seq = next_sequence(req)
    request_path = create_request(seq, commit, campaign, description, req)

    git_add_commit_push(
        [str(request_path), str(source_path)],
        f"iteration {seq:04d}: {description}",
        dry_run=dry_run,
    )
    print(f"Request {seq:04d} submitted. Polling for results...")

    if dry_run:
        print("[dry-run] Skipping poll — no push was made.")
        append_result(seq, commit, None, "dry_run", description, path=res)
        return True

    try:
        result = poll_for_completion(
            seq,
            timeout=agent_timeout(campaign),
            interval=agent_poll_interval(campaign),
            requests_dir=req,
        )
    except TimeoutError:
        print(f"Request {seq:04d} timed out.")
        append_result(seq, commit, None, "timed_out", description, path=res)
        return True

    if result.status == "failed":
        print(f"Request {seq:04d} FAILED: {result.error}")
        append_result(seq, commit, None, "failed", description, path=res)
        return True

    metric = result.metric_value
    print(f"Request {seq:04d} completed. Metric: {metric}")

    profile_summary = extract_profile_summary(result)
    if profile_summary:
        for line in format_profile_lines(profile_summary):
            print(line)

    current_best = best_result(res, direction=direction)
    best_val = float(current_best["metric_value"]) if current_best is not None else None

    append_result(seq, commit, metric, "completed", description, path=res)

    ctx = ResultContext(
        seq=seq,
        commit=commit,
        description=description,
        source_path=source_path,
        results_path=res,
        failures_path=fail,
        optimization_branch=optimization_branch(campaign),
    )
    apply_judge_verdict(metric, best_val, direction, campaign, result, ctx, dry_run=dry_run)

    if below_threshold(metric, best_val, campaign):
        threshold = metric_threshold(campaign)
        print(f"Improvement below threshold ({threshold}). Stopping early.")
        return False

    return True


def run_baseline(
    campaign: CampaignConfig,
    source_path: Path,
    dry_run: bool,
) -> None:
    """Submit a baseline request for the current commit and wait for results."""
    req = requests_dir()
    commit = git_submodule_head(source_path)
    seq = next_sequence(req)
    description = f"Baseline: unmodified {project_name(campaign)}"

    request_path = create_request(seq, commit, campaign, description, req)

    git_add_commit_push(
        [str(request_path)],
        f"baseline {seq:04d}: {description}",
        dry_run=dry_run,
    )
    print(f"Baseline request {seq:04d} submitted ({commit[:12]}).")

    if dry_run:
        print(f"[dry-run] Request written to {request_path}")
        return

    try:
        result = poll_for_completion(
            seq,
            timeout=agent_timeout(campaign),
            interval=agent_poll_interval(campaign),
            requests_dir=req,
        )
    except TimeoutError:
        print(f"Baseline request {seq:04d} timed out.")
        return

    if result.status == "failed":
        print(f"Baseline request {seq:04d} FAILED: {result.error}")
        return

    print(f"Baseline request {seq:04d} completed. Metric: {result.metric_value}")

    profile_summary = extract_profile_summary(result)
    if profile_summary:
        for line in format_profile_lines(profile_summary):
            print(line)


def main() -> None:
    """Entry point for the interactive autoforge agent."""
    parser = argparse.ArgumentParser(description="Autoforge interactive loop")
    parser.add_argument(
        "--campaign",
        default=None,
        help="Path to campaign TOML config",
    )
    parser.add_argument("--dry-run", action="store_true", help="Skip git push (local testing)")
    parser.add_argument(
        "--baseline",
        action="store_true",
        help="Submit a baseline request (no code changes) to test the pipeline",
    )
    parser.add_argument(
        "--log-level",
        choices=["debug", "info", "warning", "error"],
        default=None,
        help="Log level (default: info, or LOG_LEVEL env var)",
    )
    parser.add_argument("--log-file", default=None, help="Path to log file")
    args = parser.parse_args()

    setup_logging(args.log_level, args.log_file)

    explicit = Path(args.campaign) if args.campaign else None
    campaign = load_campaign(resolve_campaign_path(explicit))
    source_path = Path(submodule_path(campaign))
    opt_branch = optimization_branch(campaign)
    if not opt_branch:
        raise SystemExit(
            "ERROR: campaign.toml is missing project.optimization_branch. "
            "Run 'autoforge sprint init <name>' to create a properly configured sprint."
        )
    ensure_optimization_branch(source_path, opt_branch)

    if args.baseline:
        run_baseline(campaign, source_path, args.dry_run)
    else:
        while run_interactive_iteration(campaign, source_path, args.dry_run):
            pass

    print("Optimization loop finished.")


if __name__ == "__main__":
    main()
