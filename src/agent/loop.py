"""Main autoresearch optimization loop."""

from __future__ import annotations

import argparse
import logging
import subprocess
import sys
import tomllib
from pathlib import Path

from src.agent.history import append_result, best_result, load_history
from src.agent.metric import compare_metric
from src.agent.protocol import create_request, next_sequence, poll_for_completion
from src.agent.strategy import format_context, validate_change

logger = logging.getLogger(__name__)


def load_campaign(path: Path) -> dict:
    """Load and return the campaign TOML configuration."""
    with open(path, "rb") as f:
        return tomllib.load(f)


def git_submodule_head(dpdk_path: Path) -> str:
    """Return the current HEAD commit SHA of the DPDK submodule."""
    result = subprocess.run(
        ["git", "-C", str(dpdk_path), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def git_add_commit_push(
    paths: list[str],
    message: str,
    dry_run: bool = False,
) -> None:
    """Stage files, commit, and optionally push."""
    for p in paths:
        subprocess.run(["git", "add", p], check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", message],
        check=True,
        capture_output=True,
        text=True,
    )
    if not dry_run:
        subprocess.run(["git", "push"], check=True, capture_output=True, text=True)


def run_interactive_iteration(
    campaign: dict,
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

    current_best = best_result(direction=direction)
    if current_best is not None and metric is not None:
        best_val = float(current_best["metric_value"])
        if compare_metric(metric, best_val, direction):
            print(f"Improvement! {best_val} -> {metric}")
        else:
            print(f"No improvement ({metric} vs best {best_val}). Consider reverting.")

    append_result(seq, commit, metric, "completed", description)
    git_add_commit_push(["results.tsv"], f"results: iteration {seq:04d}", dry_run=dry_run)

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
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )

    campaign = load_campaign(Path(args.campaign))
    dpdk_path = Path(campaign.get("dpdk", {}).get("submodule_path", "dpdk"))

    if args.autonomous:
        run_autonomous(campaign, dpdk_path, args.dry_run, args.provider)
    else:
        while run_interactive_iteration(campaign, dpdk_path, args.dry_run):
            pass

    print("Optimization loop finished.")


def build_client(provider: str) -> tuple:
    """Build an Anthropic-compatible API client and model ID.

    Args:
        provider: "anthropic" or "openrouter".

    Returns:
        (client, model_id) tuple.
    """
    try:
        import anthropic
    except ImportError:
        print("Error: 'anthropic' package required for autonomous mode.")
        print("Install with: uv add anthropic")
        sys.exit(1)

    if provider == "openrouter":
        import os

        api_key = os.environ.get("OPENROUTER_API_KEY")
        if not api_key:
            print("Error: OPENROUTER_API_KEY environment variable required.")
            sys.exit(1)
        client = anthropic.Anthropic(
            base_url="https://openrouter.ai/api/v1",
            api_key=api_key,
        )
        model = "anthropic/claude-opus-4-6"
    else:
        client = anthropic.Anthropic()
        model = "claude-opus-4-6"

    return client, model


def run_autonomous(
    campaign: dict,
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
    client, model = build_client(provider)
    max_iter = campaign.get("campaign", {}).get("max_iterations", 50)

    for _ in range(max_iter):
        history = load_history()
        context = format_context(history, campaign)

        goal = campaign.get("goal", {}).get("description", "").strip()
        goal_block = f"\nGoal:\n{goal}\n" if goal else ""

        prompt = (
            f"You are optimizing DPDK for maximum throughput.\n"
            f"{goal_block}\n"
            f"Current state:\n{context}\n\n"
            f"Propose a specific code change to the DPDK source in {dpdk_path}. "
            f"Focus on the scoped areas. Describe the change and the file(s) to modify."
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
        append_result(seq, commit, metric, result.status, description)
        git_add_commit_push(["results.tsv"], f"results: iteration {seq:04d}", dry_run=dry_run)


if __name__ == "__main__":
    main()
