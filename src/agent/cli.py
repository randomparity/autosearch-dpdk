"""CLI subcommands for Claude Code agent integration."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from src.agent.campaign import CampaignConfig, load_campaign
from src.agent.git_ops import (
    full_revert,
    git_add_commit_push,
    git_submodule_head,
    record_result_or_revert,
)
from src.agent.history import (
    append_result,
    best_result,
    format_failures,
    load_failures,
    load_history,
)
from src.agent.protocol import (
    create_request,
    find_latest_request,
    find_request_by_seq,
    next_sequence,
    poll_for_completion,
)
from src.agent.sprint import (
    active_sprint_name,
    failures_path,
    init_sprint,
    list_sprints,
    requests_dir,
    results_path,
    switch_sprint,
)
from src.agent.strategy import (
    extract_profile_summary,
    format_context,
    format_profile_lines,
    validate_change,
)

DEFAULT_CAMPAIGN = Path(__file__).resolve().parent.parent.parent / "config" / "campaign.toml"


def _dpdk_path(campaign: CampaignConfig) -> Path:
    return Path(campaign.get("dpdk", {}).get("submodule_path", "dpdk"))


def _optimization_branch(campaign: CampaignConfig) -> str:
    return campaign.get("dpdk", {}).get("optimization_branch", "")


def _req_dir(campaign: CampaignConfig) -> Path:
    return requests_dir(campaign)


def _res_path(campaign: CampaignConfig) -> Path:
    return results_path(campaign)


def _fail_path(campaign: CampaignConfig) -> Path:
    return failures_path(campaign)


def cmd_context(campaign: CampaignConfig) -> None:
    """Print current optimization state."""
    res = _res_path(campaign)
    fail = _fail_path(campaign)
    req = _req_dir(campaign)

    history = load_history(res)
    fails = load_failures(fail)

    latest = find_latest_request(req)
    profile = extract_profile_summary(latest) if latest else None

    try:
        name = active_sprint_name(campaign)
        print(f"Sprint: {name}")
    except KeyError:
        pass

    print(format_context(history, campaign, profile_summary=profile))

    fail_text = format_failures(fails)
    if fail_text:
        print()
        print(fail_text)


def cmd_submit(campaign: CampaignConfig, description: str, dry_run: bool) -> None:
    """Validate submodule change, create request, commit, push."""
    dpdk_path = _dpdk_path(campaign)
    req = _req_dir(campaign)

    if not validate_change(dpdk_path):
        print("ERROR: No submodule change detected. Commit in the submodule first.")
        sys.exit(1)

    commit = git_submodule_head(dpdk_path)
    seq = next_sequence(req)
    request_path = create_request(seq, commit, campaign, description, req)

    git_add_commit_push(
        [str(request_path), str(dpdk_path)],
        f"iteration {seq:04d}: {description}",
        dry_run=dry_run,
    )
    print(f"Request {seq:04d} submitted (commit {commit[:12]}).")


def cmd_poll(campaign: CampaignConfig) -> None:
    """Poll until the latest request reaches a terminal state."""
    req = _req_dir(campaign)
    latest = find_latest_request(req)
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
            latest.sequence,
            timeout=timeout,
            interval=poll_interval,
            requests_dir=req,
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
    req = _req_dir(campaign)
    res = _res_path(campaign)
    fail = _fail_path(campaign)
    direction = campaign.get("metric", {}).get("direction", "maximize")

    latest = find_latest_request(req)
    if latest is None:
        print("No requests found.")
        sys.exit(1)

    if not latest.is_terminal:
        print(f"Request {latest.sequence:04d} is still {latest.status}. Run poll first.")
        sys.exit(1)

    metric = latest.metric_value if latest.status == "completed" else None
    commit = latest.dpdk_commit
    description = latest.description or ""

    current_best = best_result(res, direction=direction)
    best_val = float(current_best["metric_value"]) if current_best else None

    append_result(latest.sequence, commit, metric, latest.status, description, path=res)

    record_result_or_revert(
        metric,
        best_val,
        direction,
        latest.sequence,
        commit,
        description,
        dpdk_path,
        dry_run,
        results_path=res,
        failures_path=fail,
        optimization_branch=_optimization_branch(campaign),
    )


def cmd_baseline(campaign: CampaignConfig, dry_run: bool) -> None:
    """Submit a baseline request (no code changes) and optionally poll."""
    dpdk_path = _dpdk_path(campaign)
    req = _req_dir(campaign)
    commit = git_submodule_head(dpdk_path)
    seq = next_sequence(req)
    description = "Baseline: unmodified DPDK"

    request_path = create_request(seq, commit, campaign, description, req)
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
        result = poll_for_completion(
            seq,
            timeout=timeout,
            interval=poll_interval,
            requests_dir=req,
        )
    except TimeoutError:
        print(f"Baseline request {seq:04d} timed out.")
        return

    _print_result(result)


def cmd_revert(campaign: CampaignConfig, dry_run: bool) -> None:
    """Revert the last DPDK submodule commit and force-push the fork."""
    dpdk_path = _dpdk_path(campaign)
    branch = _optimization_branch(campaign)

    old_head = full_revert(dpdk_path, branch, dry_run)
    new_head = git_submodule_head(dpdk_path)

    print(f"Reverted {old_head[:12]} -> {new_head[:12]}")
    if branch and not dry_run:
        print(f"Force-pushed {branch} to origin.")
    elif dry_run:
        print("[dry-run] Skipped push.")


def _format_build_log(log: str) -> str:
    """Highlight error lines in a build log for readability."""
    error_patterns = ("error:", "FAILED", "fatal:", "undefined reference")
    lines = []
    for line in log.splitlines():
        if any(pat in line for pat in error_patterns):
            lines.append(f">>> {line}")
        else:
            lines.append(f"    {line}")
    return "\n".join(lines)


def cmd_build_log(campaign: CampaignConfig, seq: int) -> None:
    """Print the build log for a given request sequence number."""
    req = _req_dir(campaign)
    request = find_request_by_seq(seq, req)

    if request is None:
        print(f"ERROR: No request found for sequence {seq:04d}.")
        sys.exit(1)

    snippet = request.build_log_snippet
    if not snippet:
        print(f"No build log for request {seq:04d}.")
        return

    print(f"Build log for request {seq:04d}:")
    print(_format_build_log(snippet))


def cmd_status(campaign: CampaignConfig) -> None:
    """Print the latest request status without polling."""
    req = _req_dir(campaign)
    latest = find_latest_request(req)
    if latest is None:
        print("No requests found.")
        return
    _print_result(latest)


def cmd_sprint_init(name: str, campaign_path: Path) -> None:
    """Create a new sprint directory."""
    try:
        sdir = init_sprint(name, campaign_path)
    except (ValueError, FileExistsError) as exc:
        print(f"ERROR: {exc}")
        sys.exit(1)
    print(f"Sprint initialized: {sdir}")
    print("  requests/  docs/  campaign.toml  results.tsv")


def cmd_sprint_list(campaign: CampaignConfig) -> None:
    """List all sprints with summary."""
    sprints = list_sprints()
    if not sprints:
        print("No sprints found.")
        return

    try:
        active = active_sprint_name(campaign)
    except KeyError:
        active = None

    print("Sprints:")
    for s in sprints:
        marker = " *" if s["name"] == active else "  "
        best = f"{s['max_metric']:.2f} Mpps" if s["max_metric"] else "no data"
        label = "(active)" if s["name"] == active else ""
        print(f"{marker} {s['name']:40s} {label:10s} {s['iterations']:3d} iterations, best: {best}")


def cmd_sprint_active(campaign: CampaignConfig) -> None:
    """Print the active sprint name."""
    try:
        print(active_sprint_name(campaign))
    except KeyError as exc:
        print(f"ERROR: {exc}")
        sys.exit(1)


def main() -> None:
    """CLI entry point for autosearch subcommands."""
    parser = argparse.ArgumentParser(prog="autosearch")
    parser.add_argument(
        "--campaign",
        default=None,
        help="Path to campaign TOML config",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Skip git push",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("context", help="Print current optimization state")
    sub.add_parser("status", help="Print latest request status")
    sub.add_parser("poll", help="Poll until latest request completes")
    sub.add_parser("judge", help="Compare result to best, keep or revert")
    sub.add_parser("baseline", help="Submit baseline request")
    sub.add_parser("revert", help="Revert last DPDK change and force-push fork")

    submit_p = sub.add_parser("submit", help="Submit a code change for testing")
    submit_p.add_argument(
        "--description",
        "-d",
        required=True,
        help="Description of the change",
    )

    buildlog_p = sub.add_parser("build-log", help="Print build log for a request")
    buildlog_p.add_argument(
        "--seq",
        "-s",
        type=int,
        required=True,
        help="Request sequence number",
    )

    # Sprint subcommands
    sprint_p = sub.add_parser("sprint", help="Sprint management")
    sprint_sub = sprint_p.add_subparsers(dest="sprint_command", required=True)

    init_p = sprint_sub.add_parser("init", help="Create a new sprint")
    init_p.add_argument("name", help="Sprint name (YYYY-MM-DD-slug)")

    sprint_sub.add_parser("list", help="List all sprints")
    sprint_sub.add_parser("active", help="Print active sprint name")

    switch_p = sprint_sub.add_parser("switch", help="Switch to an existing sprint")
    switch_p.add_argument("name", help="Sprint name to switch to")

    args = parser.parse_args()

    campaign_path = Path(args.campaign) if args.campaign else DEFAULT_CAMPAIGN

    # Sprint init doesn't need full campaign loaded
    if args.command == "sprint" and args.sprint_command == "init":
        cmd_sprint_init(args.name, campaign_path)
        return

    campaign = load_campaign(campaign_path)

    if args.command == "sprint":
        if args.sprint_command == "list":
            cmd_sprint_list(campaign)
        elif args.sprint_command == "active":
            cmd_sprint_active(campaign)
        elif args.sprint_command == "switch":
            switch_sprint(args.name, campaign_path)
            print(f"Switched to sprint: {args.name}")
    elif args.command == "context":
        cmd_context(campaign)
    elif args.command == "submit":
        cmd_submit(campaign, args.description, args.dry_run)
    elif args.command == "poll":
        cmd_poll(campaign)
    elif args.command == "judge":
        cmd_judge(campaign, args.dry_run)
    elif args.command == "baseline":
        cmd_baseline(campaign, args.dry_run)
    elif args.command == "revert":
        cmd_revert(campaign, args.dry_run)
    elif args.command == "build-log":
        cmd_build_log(campaign, args.seq)
    elif args.command == "status":
        cmd_status(campaign)


if __name__ == "__main__":
    main()
