"""CLI subcommands for Claude Code agent integration."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from src.agent.campaign import CampaignConfig, load_campaign
from src.agent.git_ops import (
    git_add_commit_push,
    git_submodule_head,
    record_result_or_revert,
)
from src.agent.history import append_result, best_result, load_failures, load_history
from src.agent.protocol import (
    create_request,
    find_latest_request,
    next_sequence,
    poll_for_completion,
)
from src.agent.strategy import (
    extract_profile_summary,
    format_context,
    format_profile_lines,
    validate_change,
)


def _dpdk_path(campaign: CampaignConfig) -> Path:
    return Path(campaign.get("dpdk", {}).get("submodule_path", "dpdk"))


def cmd_context(campaign: CampaignConfig) -> None:
    """Print current optimization state."""
    history = load_history()
    failures = load_failures()

    latest = find_latest_request()
    profile = extract_profile_summary(latest) if latest else None

    print(format_context(history, campaign, profile_summary=profile))

    from src.agent.history import format_failures

    fail_text = format_failures(failures)
    if fail_text:
        print()
        print(fail_text)


def cmd_submit(campaign: CampaignConfig, description: str, dry_run: bool) -> None:
    """Validate submodule change, create request, commit, push."""
    dpdk_path = _dpdk_path(campaign)

    if not validate_change(dpdk_path):
        print("ERROR: No submodule change detected. Commit in the submodule first.")
        sys.exit(1)

    commit = git_submodule_head(dpdk_path)
    seq = next_sequence()
    request_path = create_request(seq, commit, campaign, description)

    git_add_commit_push(
        [str(request_path), str(dpdk_path)],
        f"iteration {seq:04d}: {description}",
        dry_run=dry_run,
    )
    print(f"Request {seq:04d} submitted (commit {commit[:12]}).")


def cmd_poll(campaign: CampaignConfig) -> None:
    """Poll until the latest request reaches a terminal state."""
    latest = find_latest_request()
    if latest is None:
        print("No requests found.")
        sys.exit(1)

    if latest.is_terminal:
        _print_result(latest)
        return

    poll_interval = campaign.get("agent", {}).get("poll_interval", 30)
    timeout = campaign.get("agent", {}).get("timeout_minutes", 60) * 60

    try:
        result = poll_for_completion(
            latest.sequence, timeout=timeout, interval=poll_interval,
        )
    except TimeoutError:
        print(f"Request {latest.sequence:04d} timed out.")
        sys.exit(1)

    _print_result(result)


def _print_result(result: object) -> None:
    """Print a completed/failed request result."""
    seq = getattr(result, "sequence", "?")
    status = getattr(result, "status", "?")
    metric = getattr(result, "metric_value", None)
    error = getattr(result, "error", None)

    if status == "failed":
        print(f"Request {seq:04d} FAILED: {error}")
    else:
        print(f"Request {seq:04d} {status}. Metric: {metric}")

    profile = extract_profile_summary(result)
    if profile:
        for line in format_profile_lines(profile):
            print(line)


def cmd_judge(campaign: CampaignConfig, dry_run: bool) -> None:
    """Compare latest result to best, keep or revert, record in TSV."""
    dpdk_path = _dpdk_path(campaign)
    direction = campaign.get("metric", {}).get("direction", "maximize")

    latest = find_latest_request()
    if latest is None:
        print("No requests found.")
        sys.exit(1)

    if not latest.is_terminal:
        print(f"Request {latest.sequence:04d} is still {latest.status}. Run poll first.")
        sys.exit(1)

    metric = latest.metric_value if latest.status == "completed" else None
    commit = latest.dpdk_commit
    description = latest.description or ""

    current_best = best_result(direction=direction)
    best_val = float(current_best["metric_value"]) if current_best else None

    append_result(latest.sequence, commit, metric, latest.status, description)

    record_result_or_revert(
        metric, best_val, direction,
        latest.sequence, commit, description, dpdk_path, dry_run,
    )


def cmd_baseline(campaign: CampaignConfig, dry_run: bool) -> None:
    """Submit a baseline request (no code changes) and optionally poll."""
    dpdk_path = _dpdk_path(campaign)
    commit = git_submodule_head(dpdk_path)
    seq = next_sequence()
    description = "Baseline: unmodified DPDK"

    request_path = create_request(seq, commit, campaign, description)
    git_add_commit_push(
        [str(request_path)],
        f"baseline {seq:04d}: {description}",
        dry_run=dry_run,
    )
    print(f"Baseline request {seq:04d} submitted (commit {commit[:12]}).")

    if dry_run:
        print(f"[dry-run] Request written to {request_path}")
        return

    poll_interval = campaign.get("agent", {}).get("poll_interval", 30)
    timeout = campaign.get("agent", {}).get("timeout_minutes", 60) * 60

    try:
        result = poll_for_completion(seq, timeout=timeout, interval=poll_interval)
    except TimeoutError:
        print(f"Baseline request {seq:04d} timed out.")
        return

    _print_result(result)


def cmd_status(campaign: CampaignConfig) -> None:
    """Print the latest request status without polling."""
    latest = find_latest_request()
    if latest is None:
        print("No requests found.")
        return

    _print_result(latest)


def main() -> None:
    """CLI entry point for autosearch subcommands."""
    parser = argparse.ArgumentParser(prog="autosearch")
    parser.add_argument(
        "--campaign", default=None, help="Path to campaign TOML config",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Skip git push",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("context", help="Print current optimization state")
    sub.add_parser("status", help="Print latest request status")
    sub.add_parser("poll", help="Poll until latest request completes")
    sub.add_parser("judge", help="Compare result to best, keep or revert")
    sub.add_parser("baseline", help="Submit baseline request")

    submit_p = sub.add_parser("submit", help="Submit a code change for testing")
    submit_p.add_argument(
        "--description", "-d", required=True, help="Description of the change",
    )

    args = parser.parse_args()

    campaign_path = Path(args.campaign) if args.campaign else None
    campaign = load_campaign(campaign_path)

    if args.command == "context":
        cmd_context(campaign)
    elif args.command == "submit":
        cmd_submit(campaign, args.description, args.dry_run)
    elif args.command == "poll":
        cmd_poll(campaign)
    elif args.command == "judge":
        cmd_judge(campaign, args.dry_run)
    elif args.command == "baseline":
        cmd_baseline(campaign, args.dry_run)
    elif args.command == "status":
        cmd_status(campaign)


if __name__ == "__main__":
    main()
