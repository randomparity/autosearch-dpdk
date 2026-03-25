"""Git operations for the agent optimization loop."""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from src.agent.history import append_failure
from src.agent.metric import compare_metric

logger = logging.getLogger(__name__)

GIT_TIMEOUT = 60


def git_submodule_head(dpdk_path: Path) -> str:
    """Return the current HEAD commit SHA of the DPDK submodule."""
    result = subprocess.run(
        ["git", "-C", str(dpdk_path), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
        timeout=GIT_TIMEOUT,
    )
    return result.stdout.strip()


def git_add_commit_push(
    paths: list[str],
    message: str,
    dry_run: bool = False,
) -> None:
    """Stage files, commit, and optionally push."""
    for p in paths:
        subprocess.run(
            ["git", "add", p],
            check=True,
            capture_output=True,
            timeout=GIT_TIMEOUT,
        )
    subprocess.run(
        ["git", "commit", "-m", message],
        check=True,
        capture_output=True,
        text=True,
        timeout=GIT_TIMEOUT,
    )
    if not dry_run:
        subprocess.run(
            ["git", "push"],
            check=True,
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT,
        )


def ensure_optimization_branch(dpdk_path: Path, branch: str) -> None:
    """Create and check out the optimization branch if it doesn't exist."""
    result = subprocess.run(
        ["git", "-C", str(dpdk_path), "branch", "--list", branch],
        capture_output=True,
        text=True,
        timeout=GIT_TIMEOUT,
    )
    if not result.stdout.strip():
        logger.info("Creating optimization branch %s in %s", branch, dpdk_path)
        subprocess.run(
            ["git", "-C", str(dpdk_path), "checkout", "-b", branch],
            check=True,
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT,
        )
    else:
        current = subprocess.run(
            ["git", "-C", str(dpdk_path), "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT,
        )
        if current.stdout.strip() != branch:
            subprocess.run(
                ["git", "-C", str(dpdk_path), "checkout", branch],
                check=True,
                capture_output=True,
                text=True,
                timeout=GIT_TIMEOUT,
            )


def get_diff_summary(dpdk_path: Path) -> str:
    """Capture a short diff stat of the last commit vs its parent."""
    result = subprocess.run(
        ["git", "-C", str(dpdk_path), "diff", "--stat", "HEAD~1", "HEAD"],
        capture_output=True,
        text=True,
        timeout=GIT_TIMEOUT,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def revert_last_change(dpdk_path: Path) -> None:
    """Reset the DPDK submodule to the previous commit."""
    subprocess.run(
        ["git", "-C", str(dpdk_path), "reset", "--hard", "HEAD~1"],
        check=True,
        capture_output=True,
        text=True,
        timeout=GIT_TIMEOUT,
    )
    logger.info("Reverted DPDK submodule to %s", git_submodule_head(dpdk_path)[:12])


def record_result_or_revert(
    metric: float | None,
    best_val: float | None,
    direction: str,
    seq: int,
    commit: str,
    description: str,
    dpdk_path: Path,
    dry_run: bool,
    results_path: Path,
    failures_path: Path,
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
        files = [str(results_path), str(dpdk_path)]
        git_add_commit_push(files, f"results: iteration {seq:04d}", dry_run=dry_run)
    else:
        print(f"No improvement ({metric} vs best {best_val}). Reverting.")
        diff_summary = get_diff_summary(dpdk_path)
        revert_last_change(dpdk_path)
        append_failure(commit, metric, description, diff_summary, path=failures_path)
        files = [str(results_path), str(failures_path), str(dpdk_path)]
        git_add_commit_push(files, f"revert: iteration {seq:04d}", dry_run=dry_run)

    return improved
