"""CLI subcommands for Claude Code agent integration."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from autoforge.agent.git_ops import (
    DirtyWorkingTreeError,
    ResultContext,
    check_git_clean,
    full_revert,
    git_add_commit_push,
    git_submodule_head,
    push_submodule,
)
from autoforge.agent.hints import hints_file_ref, list_topics
from autoforge.agent.history import (
    append_result,
    best_result,
    format_failures,
    load_failures,
    load_history,
)
from autoforge.agent.judge import apply_judge_verdict
from autoforge.agent.project import init_project, list_projects, switch_project
from autoforge.agent.protocol import (
    create_request,
    find_latest_request,
    find_request_by_seq,
    next_sequence,
    poll_for_completion,
)
from autoforge.agent.sprint import (
    active_sprint_name,
    failures_path,
    init_sprint,
    list_sprints,
    requests_dir,
    results_path,
    switch_sprint,
)
from autoforge.agent.strategy import (
    extract_profile_summary,
    format_context,
    format_failure_patterns,
    format_profile_lines,
    has_submodule_change,
)
from autoforge.campaign import (
    CampaignConfig,
    agent_poll_interval,
    agent_timeout,
    load_campaign,
    metric_direction,
    optimization_branch,
    platform_arch,
    project_name,
    resolve_campaign_path,
    submodule_path,
)
from autoforge.pointer import load_pointer
from autoforge.protocol import Direction, TestRequest


def cmd_context(campaign: CampaignConfig) -> None:
    """Print current optimization state."""
    res = results_path()
    fail = failures_path()
    req = requests_dir()

    history = load_history(res)
    fails = load_failures(fail)

    latest = find_latest_request(req)
    profile = extract_profile_summary(latest) if latest else None

    try:
        name = active_sprint_name()
        print(f"Sprint: {name}")
    except (KeyError, FileNotFoundError):
        print("Sprint: (not configured)")

    print(format_context(history, campaign, profile_summary=profile))

    fail_text = format_failures(fails)
    if fail_text:
        print()
        print(fail_text)

    failure_patterns = format_failure_patterns(req)
    if failure_patterns:
        print()
        print(failure_patterns)


def cmd_submit(
    campaign: CampaignConfig,
    description: str,
    dry_run: bool,
    tags: str | None = None,
) -> None:
    """Validate submodule change, create request, commit, push."""
    if not dry_run:
        check_git_clean()
    source_path = Path(submodule_path(campaign))
    req = requests_dir()

    if not has_submodule_change(source_path):
        print("ERROR: No submodule change detected. Commit in the submodule first.")
        sys.exit(1)

    commit = git_submodule_head(source_path)
    branch = optimization_branch(campaign)
    if branch and not dry_run:
        push_submodule(source_path, branch)

    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else None

    seq = next_sequence(req)
    request_path = create_request(seq, commit, campaign, description, req, tags=tag_list)

    git_add_commit_push(
        [str(request_path), str(source_path)],
        f"iteration {seq:04d}: {description}",
        dry_run=dry_run,
    )
    print(f"Request {seq:04d} submitted (commit {commit[:12]}).")


def cmd_poll(campaign: CampaignConfig) -> None:
    """Poll until the latest request reaches a terminal state."""
    check_git_clean()
    req = requests_dir()
    latest = find_latest_request(req)
    if latest is None:
        print("No requests found.")
        sys.exit(1)

    if latest.is_terminal:
        _print_result(latest)
        return

    try:
        result = poll_for_completion(
            latest.sequence,
            timeout=agent_timeout(campaign),
            interval=agent_poll_interval(campaign),
            requests_dir=req,
        )
    except TimeoutError:
        print(f"Request {latest.sequence:04d} timed out.")
        sys.exit(1)

    _print_result(result)


def _format_timeline(request: TestRequest) -> str:
    """Build a compact phase timeline string with durations."""
    phases: list[tuple[str, str | None]] = [
        ("created", request.created_at),
        ("claimed", request.claimed_at),
        ("built", request.built_at),
        ("deployed", request.deployed_at),
        ("completed", request.completed_at),
    ]
    parts: list[str] = []
    prev_dt: datetime | None = None
    for label, ts in phases:
        if ts is None:
            continue
        try:
            dt = datetime.fromisoformat(ts)
        except ValueError:
            parts.append(f"{label} {ts}")
            prev_dt = None
            continue
        time_str = dt.strftime("%H:%M:%S")
        if prev_dt is not None:
            delta = dt - prev_dt
            secs = int(delta.total_seconds())
            dur = f"{secs // 60}m{secs % 60}s" if secs >= 60 else f"{secs}s"
            parts.append(f"{label} {time_str} (+{dur})")
        else:
            parts.append(f"{label} {time_str}")
        prev_dt = dt

    if request.status == "failed" and request.failed_phase:
        parts[-1] = f"FAILED at {request.failed_phase} {parts[-1].split(' ', 1)[1]}"

    return " -> ".join(parts)


def _failure_log(request: TestRequest) -> str | None:
    """Return the appropriate log snippet based on failed_phase."""
    phase = request.failed_phase
    if phase == "build":
        return request.build_log_snippet
    if phase == "deploy":
        return request.deploy_log_snippet
    if phase == "test":
        return request.test_log_snippet
    # Fallback: return whichever is available
    return request.build_log_snippet or request.deploy_log_snippet or request.test_log_snippet


def _print_result(result: TestRequest) -> None:
    """Print a completed/failed request result."""
    seq = result.sequence
    status = result.status
    metric = result.metric_value
    error = result.error

    if status == "failed":
        phase_info = f" at {result.failed_phase}" if result.failed_phase else ""
        print(f"Request {seq:04d} FAILED{phase_info}: {error}")
    else:
        print(f"Request {seq:04d} {status}. Metric: {metric}")

    timeline = _format_timeline(result)
    if timeline:
        print(f"  Timeline: {timeline}")

    if status == "failed":
        log = _failure_log(result)
        if log:
            lines = log.splitlines()
            tail = lines[-30:] if len(lines) > 30 else lines
            print(f"\n  Log ({len(lines)} lines, showing last {len(tail)}):")
            print(
                _format_log(
                    "\n".join(tail),
                    _error_patterns_for_phase(result.failed_phase),
                )
            )

    profile = extract_profile_summary(result)
    if profile:
        for line in format_profile_lines(profile):
            print(line)


def cmd_judge(campaign: CampaignConfig, dry_run: bool) -> None:
    """Compare latest result to best, keep or revert, record in TSV."""
    if not dry_run:
        check_git_clean()
    source_path = Path(submodule_path(campaign))
    req = requests_dir()
    res = results_path()
    fail = failures_path()
    direction: Direction = metric_direction(campaign)

    latest = find_latest_request(req)
    if latest is None:
        print("No requests found.")
        sys.exit(1)

    if not latest.is_terminal:
        print(f"Request {latest.sequence:04d} is still {latest.status}. Run poll first.")
        sys.exit(1)

    metric = latest.metric_value if latest.status == "completed" else None
    commit = latest.source_commit
    description = latest.description or ""
    req_tags = getattr(latest, "tags", None)

    current_best = best_result(res, direction=direction)
    best_val = float(current_best["metric_value"]) if current_best else None

    append_result(
        latest.sequence,
        commit,
        metric,
        latest.status,
        description,
        path=res,
        tags=req_tags,
    )

    ctx = ResultContext(
        seq=latest.sequence,
        commit=commit,
        description=description,
        source_path=source_path,
        results_path=res,
        failures_path=fail,
        optimization_branch=optimization_branch(campaign),
    )

    apply_judge_verdict(metric, best_val, direction, campaign, latest, ctx, dry_run=dry_run)


def _poll_and_record(
    campaign: CampaignConfig,
    seq: int,
    req: Path,
    description: str,
    label: str,
    dry_run: bool,
    request_path: Path,
) -> None:
    """Poll for request completion and record result in TSV history."""
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
        print(f"{label.capitalize()} request {seq:04d} timed out.")
        return

    _print_result(result)

    if result.status == "completed" and result.metric_value is not None:
        res = results_path()
        append_result(
            result.sequence,
            result.source_commit,
            result.metric_value,
            result.status,
            result.description or description,
            path=res,
        )
        git_add_commit_push(
            [str(res)],
            f"{label} {seq:04d}: recorded result",
            dry_run=False,
        )
        print(f"{label.capitalize()} recorded in results.tsv.")
    elif result.status == "failed":
        print(f"{label.capitalize()} failed — not recorded. Fix the issue and retry.")


def cmd_baseline(campaign: CampaignConfig, dry_run: bool) -> None:
    """Submit a baseline request (no code changes) and optionally poll."""
    if not dry_run:
        check_git_clean()
    source_path = Path(submodule_path(campaign))
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
    print(f"Baseline request {seq:04d} submitted (commit {commit[:12]}).")

    _poll_and_record(campaign, seq, req, description, "baseline", dry_run, request_path)


def cmd_finale(campaign: CampaignConfig, dry_run: bool) -> None:
    """Submit a finale request (modified source, no profiling) and poll."""
    if not dry_run:
        check_git_clean()
    source_path = Path(submodule_path(campaign))
    req = requests_dir()

    if not has_submodule_change(source_path):
        print("ERROR: No submodule change detected. Commit in the submodule first.")
        sys.exit(1)

    commit = git_submodule_head(source_path)
    branch = optimization_branch(campaign)
    if branch and not dry_run:
        push_submodule(source_path, branch)

    seq = next_sequence(req)
    description = "Finale: modified source, no profiling"

    request_path = create_request(
        seq,
        commit,
        campaign,
        description,
        req,
        skip_profiling=True,
    )
    git_add_commit_push(
        [str(request_path), str(source_path)],
        f"finale {seq:04d}: {description}",
        dry_run=dry_run,
    )
    print(f"Finale request {seq:04d} submitted (commit {commit[:12]}).")

    _poll_and_record(campaign, seq, req, description, "finale", dry_run, request_path)


def cmd_revert(campaign: CampaignConfig, dry_run: bool) -> None:
    """Revert the last DPDK submodule commit and force-push the fork."""
    if not dry_run:
        check_git_clean()
    source_path = Path(submodule_path(campaign))
    branch = optimization_branch(campaign)

    old_head = full_revert(source_path, branch, dry_run)
    new_head = git_submodule_head(source_path)

    print(f"Reverted {old_head[:12]} -> {new_head[:12]}")
    if branch and not dry_run:
        print(f"Force-pushed {branch} to origin.")
    elif dry_run:
        print("[dry-run] Skipped push.")


_BUILD_ERROR_PATTERNS = ("error:", "FAILED", "fatal:", "undefined reference")
_DEPLOY_ERROR_PATTERNS = ("error:", "FAILED", "fatal:", "timeout", "refused")
_TEST_ERROR_PATTERNS = ("error:", "FAILED", "FAIL", "timeout", "assertion")


def _error_patterns_for_phase(phase: str | None) -> tuple[str, ...]:
    """Return error highlight patterns for a given phase."""
    if phase == "deploy":
        return _DEPLOY_ERROR_PATTERNS
    if phase == "test":
        return _TEST_ERROR_PATTERNS
    return _BUILD_ERROR_PATTERNS


def _format_log(log: str, error_patterns: tuple[str, ...] | None = None) -> str:
    """Highlight error lines in a log for readability."""
    patterns = error_patterns or _BUILD_ERROR_PATTERNS
    lines = []
    for line in log.splitlines():
        if any(pat in line for pat in patterns):
            lines.append(f">>> {line}")
        else:
            lines.append(f"    {line}")
    return "\n".join(lines)


def _get_log_for_phase(
    request: TestRequest,
    phase: str,
) -> str | None:
    """Return the log snippet for a given phase."""
    if phase == "build":
        return request.build_log_snippet
    if phase == "deploy":
        return request.deploy_log_snippet
    if phase == "test":
        return request.test_log_snippet
    return None


def cmd_logs(
    campaign: CampaignConfig,
    seq: int,
    phase: str | None = None,
    grep: str | None = None,
    tail: int | None = None,
) -> None:
    """Print logs for a given request, optionally filtered by phase."""
    req = requests_dir()
    request = find_request_by_seq(seq, req)

    if request is None:
        print(f"ERROR: No request found for sequence {seq:04d}.")
        sys.exit(1)

    # Auto-detect phase from failed_phase, or show all available
    phases_to_show: list[str] = []
    if phase:
        phases_to_show = [phase]
    elif request.failed_phase:
        phases_to_show = [request.failed_phase]
    else:
        for p in ("build", "deploy", "test"):
            if _get_log_for_phase(request, p):
                phases_to_show.append(p)

    if not phases_to_show:
        print(f"No logs available for request {seq:04d}.")
        return

    for p in phases_to_show:
        snippet = _get_log_for_phase(request, p)
        if not snippet:
            print(f"No {p} log for request {seq:04d}.")
            continue

        lines = snippet.splitlines()
        if grep:
            lines = [line for line in lines if grep in line]
        if tail is not None:
            lines = lines[-tail:]

        print(f"{p.capitalize()} log for request {seq:04d} ({len(lines)} lines):")
        print(_format_log("\n".join(lines), _error_patterns_for_phase(p)))


def _format_inspect(request: TestRequest) -> str:
    """Format a full human-readable view of a request."""
    lines = [
        f"Request {request.sequence:04d}",
        f"  Status:        {request.status}",
        f"  Description:   {request.description}",
        f"  Source commit:  {request.source_commit}",
        f"  Created at:    {request.created_at}",
    ]

    if request.tags:
        lines.append(f"  Tags:          {', '.join(request.tags)}")

    lines.append(
        f"  Plugins:       build={request.build_plugin} deploy={request.deploy_plugin} "
        f"test={request.test_plugin}"
    )
    if request.profile_plugin:
        lines.append(f"                 profile={request.profile_plugin}")

    lines.append("")
    lines.append("  Timeline:")
    timeline = _format_timeline(request)
    if timeline:
        lines.append(f"    {timeline}")

    if request.failed_phase:
        lines.append(f"  Failed phase:  {request.failed_phase}")
    if request.error:
        lines.append(f"  Error:         {request.error}")
    if request.metric_value is not None:
        lines.append(f"  Metric:        {request.metric_value}")
    if request.results_summary:
        lines.append(f"  Summary:       {request.results_summary}")

    runner_ids = []
    if request.build_runner_id:
        runner_ids.append(f"build={request.build_runner_id}")
    if request.deploy_runner_id:
        runner_ids.append(f"deploy={request.deploy_runner_id}")
    if request.test_runner_id:
        runner_ids.append(f"test={request.test_runner_id}")
    if runner_ids:
        lines.append(f"  Runners:       {' '.join(runner_ids)}")

    max_log_lines = 50
    for phase in ("build", "deploy", "test"):
        log = _get_log_for_phase(request, phase)
        if log:
            log_lines = log.splitlines()
            shown = log_lines[:max_log_lines]
            lines.append(f"\n  {phase.capitalize()} log ({len(log_lines)} lines):")
            lines.append(
                _format_log(
                    "\n".join(shown),
                    _error_patterns_for_phase(phase),
                )
            )
            if len(log_lines) > max_log_lines:
                lines.append(
                    f"    ... ({len(log_lines) - max_log_lines} more lines, "
                    f"use `logs --seq {request.sequence} --phase {phase}` for full output)"
                )

    if request.results_json:
        lines.append("\n  Results JSON:")
        formatted = json.dumps(request.results_json, indent=2)
        for line in formatted.splitlines():
            lines.append(f"    {line}")

    return "\n".join(lines)


def cmd_inspect(campaign: CampaignConfig, seq: int, as_json: bool = False) -> None:
    """Print full details for a request."""
    req = requests_dir()
    request = find_request_by_seq(seq, req)

    if request is None:
        print(f"ERROR: No request found for sequence {seq:04d}.")
        sys.exit(1)

    if as_json:
        print(request.to_json())
    else:
        print(_format_inspect(request))


def cmd_hints(
    campaign: CampaignConfig,
    arch_override: str | None,
    topic: str = "optimization",
    show_topics: bool = False,
) -> None:
    """Print architecture-specific optimization hints location."""
    arch = arch_override or platform_arch(campaign)
    if not arch:
        print("ERROR: No arch specified. Set [platform] arch in campaign.toml or pass --arch.")
        sys.exit(1)
    try:
        if show_topics:
            topics = list_topics(arch)
            print(f"Available hint topics for {arch}:")
            for t in topics:
                print(f"  - {t}")
        else:
            print(hints_file_ref(arch, topic))
    except (ValueError, FileNotFoundError) as exc:
        print(f"ERROR: {exc}")
        sys.exit(1)


def cmd_status(campaign: CampaignConfig) -> None:
    """Print the latest request status without polling."""
    req = requests_dir()
    latest = find_latest_request(req)
    if latest is None:
        print("No requests found.")
        return
    _print_result(latest)


def cmd_sysinfo(role: str) -> None:
    """Collect and save system info for the given role."""
    import json

    from autoforge.agent.sprint import docs_dir
    from autoforge.agent.sysinfo import save_sysinfo

    path = save_sysinfo(role, docs_dir())
    data = json.loads(path.read_text())
    print(json.dumps(data, indent=2))
    print(f"\nSaved to {path}")


def cmd_summarize(campaign: CampaignConfig) -> None:
    """Generate sprint summary from results data."""
    from autoforge.agent.sprint import docs_dir
    from autoforge.agent.summarize import generate_summary

    text = generate_summary(campaign)
    output = docs_dir() / "summary.md"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(text)
    print(f"Summary written to {output}")


def cmd_sprint_init(
    name: str,
    template: Path | None = None,
    from_sprint: str | None = None,
) -> None:
    """Create a new sprint directory."""
    try:
        sdir = init_sprint(name, template=template, from_sprint=from_sprint)
    except (ValueError, FileExistsError, FileNotFoundError) as exc:
        print(f"ERROR: {exc}")
        sys.exit(1)
    print(f"Sprint initialized: {sdir}")
    print("  requests/  docs/  campaign.toml  results.tsv")


def cmd_project_init(name: str) -> None:
    """Create a new project directory skeleton."""
    try:
        pdir = init_project(name)
    except (ValueError, FileExistsError) as exc:
        print(f"ERROR: {exc}")
        sys.exit(1)
    print(f"Project initialized: {pdir}")
    print("  builds/  deploys/  tests/  perfs/  judges/  sprints/")


def cmd_sprint_list() -> None:
    """List all sprints with summary."""
    sprints = list_sprints()
    if not sprints:
        print("No sprints found.")
        return

    try:
        active = active_sprint_name()
    except (KeyError, FileNotFoundError):
        active = None

    print("Sprints:")
    for s in sprints:
        marker = " *" if s["name"] == active else "  "
        best = f"{s['max_metric']:.2f} Mpps" if s["max_metric"] else "no data"
        label = "(active)" if s["name"] == active else ""
        print(f"{marker} {s['name']:40s} {label:10s} {s['iterations']:3d} iterations, best: {best}")


def cmd_sprint_active() -> None:
    """Print the active sprint name."""
    try:
        print(active_sprint_name())
    except (KeyError, FileNotFoundError) as exc:
        print(f"ERROR: {exc}")
        sys.exit(1)


def main() -> None:
    """CLI entry point for autoforge subcommands."""
    parser = argparse.ArgumentParser(prog="autoforge")
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
    sub.add_parser("baseline", help="Submit baseline request (unmodified source, no profiling)")
    sub.add_parser("summarize", help="Generate sprint summary from results")
    sub.add_parser("finale", help="Submit finale request (modified source, no profiling)")
    sub.add_parser("revert", help="Revert last DPDK change and force-push fork")

    hints_p = sub.add_parser("hints", help="Show architecture optimization hints")
    hints_p.add_argument(
        "--arch",
        default=None,
        help="Override arch (default: from campaign.toml [platform] arch)",
    )
    hints_p.add_argument(
        "--topic",
        default="optimization",
        help="Hint topic (default: optimization). Use --list to see available topics.",
    )
    hints_p.add_argument(
        "--list",
        action="store_true",
        dest="list_topics",
        help="List available hint topics for the architecture",
    )

    sysinfo_p = sub.add_parser("sysinfo", help="Collect and save system info")
    sysinfo_p.add_argument(
        "--role",
        required=True,
        choices=["agent", "build", "test", "runner"],
        help="Machine role (runner = build + test on same host)",
    )

    submit_p = sub.add_parser("submit", help="Submit a code change for testing")
    submit_p.add_argument(
        "--description",
        "-d",
        required=True,
        help="Description of the change",
    )
    submit_p.add_argument(
        "--tags",
        "-t",
        default=None,
        help="Comma-separated experiment tags (e.g., memcpy,cache,batching)",
    )

    logs_p = sub.add_parser("logs", help="Print logs for a request")
    logs_p.add_argument("--seq", "-s", type=int, required=True, help="Request sequence number")
    logs_p.add_argument(
        "--phase",
        "-p",
        choices=["build", "deploy", "test"],
        default=None,
        help="Phase to show (default: auto-detect from failed_phase, or all available)",
    )
    logs_p.add_argument("--grep", "-g", default=None, help="Filter lines by substring")
    logs_p.add_argument("--tail", "-n", type=int, default=None, help="Show only last N lines")

    # Keep build-log as alias for backward compat
    buildlog_p = sub.add_parser("build-log", help="Print build log for a request (alias for logs)")
    buildlog_p.add_argument("--seq", "-s", type=int, required=True, help="Request sequence number")

    inspect_p = sub.add_parser("inspect", help="Show full details for a request")
    inspect_p.add_argument("--seq", "-s", type=int, required=True, help="Request sequence number")
    inspect_p.add_argument("--json", action="store_true", dest="as_json", help="Output raw JSON")

    # Sprint subcommands
    sprint_p = sub.add_parser("sprint", help="Sprint management")
    sprint_sub = sprint_p.add_subparsers(dest="sprint_command", required=True)

    init_p = sprint_sub.add_parser("init", help="Create a new sprint")
    init_p.add_argument("name", help="Sprint name (YYYY-MM-DD-slug)")
    init_p.add_argument(
        "--from",
        dest="from_sprint",
        default=None,
        help="Clone campaign.toml from an existing sprint",
    )
    init_p.add_argument(
        "--template",
        default=None,
        help="Path to a campaign.toml template",
    )

    sprint_sub.add_parser("list", help="List all sprints")
    sprint_sub.add_parser("active", help="Print active sprint name")

    switch_p = sprint_sub.add_parser("switch", help="Switch to an existing sprint")
    switch_p.add_argument("name", help="Sprint name to switch to")

    # Doctor command
    doctor_p = sub.add_parser("doctor", help="Validate configuration setup")
    doctor_p.add_argument(
        "--role",
        choices=["agent", "runner", "all"],
        default="all",
        help="Check scope (default: all)",
    )

    # Project subcommands
    project_p = sub.add_parser("project", help="Project management")
    project_sub = project_p.add_subparsers(dest="project_command", required=True)

    project_init_p = project_sub.add_parser("init", help="Create a new project")
    project_init_p.add_argument("name", help="Project name (lowercase alphanumeric + hyphens)")

    project_sub.add_parser("list", help="List all projects")

    project_switch_p = project_sub.add_parser("switch", help="Switch the active project")
    project_switch_p.add_argument("name", help="Project name to switch to")

    args = parser.parse_args()

    try:
        _dispatch(args)
    except DirtyWorkingTreeError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)


def _dispatch(args: argparse.Namespace) -> None:
    """Route parsed CLI arguments to the appropriate command handler."""
    campaign_path = Path(args.campaign) if args.campaign else None

    # Commands that don't need campaign loaded
    if args.command == "sprint":
        if args.sprint_command == "init":
            template = Path(args.template) if args.template else None
            cmd_sprint_init(args.name, template=template, from_sprint=args.from_sprint)
        elif args.sprint_command == "list":
            cmd_sprint_list()
        elif args.sprint_command == "active":
            cmd_sprint_active()
        elif args.sprint_command == "switch":
            switch_sprint(args.name)
            print(f"Switched to sprint: {args.name}")
        return

    if args.command == "project":
        if args.project_command == "list":
            try:
                active = load_pointer().get("project")
            except (FileNotFoundError, KeyError):
                active = None
            projects = list_projects()
            if not projects:
                print("No projects found.")
            else:
                print("Projects:")
                for p in projects:
                    marker = " *" if p == active else "  "
                    print(f"{marker} {p}")
        elif args.project_command == "init":
            cmd_project_init(args.name)
        elif args.project_command == "switch":
            try:
                switch_project(args.name)
            except (ValueError, FileNotFoundError) as exc:
                print(f"ERROR: {exc}")
                sys.exit(1)
            print(f"Switched to project: {args.name}")
        return

    if args.command == "sysinfo":
        cmd_sysinfo(args.role)
        return

    if args.command == "doctor":
        from autoforge.agent.doctor import format_results, run_doctor

        results, effective_config = run_doctor(role=args.role)
        print(format_results(results, effective_config))
        if any(r.status == "fail" for r in results):
            sys.exit(1)
        return

    campaign = load_campaign(resolve_campaign_path(campaign_path))

    if args.command == "hints":
        cmd_hints(campaign, args.arch, args.topic, args.list_topics)
    elif args.command == "context":
        cmd_context(campaign)
    elif args.command == "submit":
        cmd_submit(campaign, args.description, args.dry_run, tags=args.tags)
    elif args.command == "poll":
        cmd_poll(campaign)
    elif args.command == "judge":
        cmd_judge(campaign, args.dry_run)
    elif args.command == "baseline":
        cmd_baseline(campaign, args.dry_run)
    elif args.command == "finale":
        cmd_finale(campaign, args.dry_run)
    elif args.command == "revert":
        cmd_revert(campaign, args.dry_run)
    elif args.command == "logs":
        cmd_logs(campaign, args.seq, phase=args.phase, grep=args.grep, tail=args.tail)
    elif args.command == "build-log":
        cmd_logs(campaign, args.seq, phase="build")
    elif args.command == "inspect":
        cmd_inspect(campaign, args.seq, as_json=args.as_json)
    elif args.command == "summarize":
        cmd_summarize(campaign)
    elif args.command == "status":
        cmd_status(campaign)


if __name__ == "__main__":
    main()
