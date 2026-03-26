"""Git operations for the agent optimization loop."""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from autoforge.agent.history import append_failure
from autoforge.agent.metric import Direction, compare_metric
from autoforge.protocol import GIT_TIMEOUT

logger = logging.getLogger(__name__)


class DirtyWorkingTreeError(RuntimeError):
    """Raised when a git operation requires a clean working tree."""


def check_git_clean() -> None:
    """Verify the working tree has no unstaged changes that block pull/push.

    Untracked files (``??``) are ignored — they don't block
    ``git pull --rebase``. Only modified or staged tracked files matter.

    Raises DirtyWorkingTreeError with an actionable message if dirty.
    """
    result = subprocess.run(
        ["git", "status", "--porcelain", "--ignore-submodules"],
        capture_output=True,
        text=True,
        timeout=GIT_TIMEOUT,
    )
    if result.returncode != 0:
        raise DirtyWorkingTreeError(
            f"git status failed (exit {result.returncode}): {result.stderr.strip()}"
        )
    dirty = [
        line for line in result.stdout.splitlines() if line.strip() and not line.startswith("??")
    ]
    if dirty:
        files = "\n  ".join(dirty)
        msg = (
            f"Working tree is dirty — git push/pull will fail.\n"
            f"  {files}\n"
            f"Fix: commit or stash these changes, then retry."
        )
        raise DirtyWorkingTreeError(msg)


def git_submodule_head(source_path: Path) -> str:
    """Return the current HEAD commit SHA of the DPDK submodule."""
    result = subprocess.run(
        ["git", "-C", str(source_path), "rev-parse", "HEAD"],
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


def push_submodule(source_path: Path, branch: str) -> None:
    """Push the submodule's optimization branch to its remote.

    Uses a regular push (not force). Force-push is only used during reverts.

    Args:
        source_path: Path to the submodule directory.
        branch: Branch name to push.
    """
    subprocess.run(
        ["git", "-C", str(source_path), "push", "origin", branch],
        check=True,
        capture_output=True,
        text=True,
        timeout=GIT_TIMEOUT,
    )
    logger.info("Pushed %s to origin in %s", branch, source_path)


def ensure_optimization_branch(source_path: Path, branch: str) -> None:
    """Create and check out the optimization branch if it doesn't exist."""
    result = subprocess.run(
        ["git", "-C", str(source_path), "branch", "--list", branch],
        capture_output=True,
        text=True,
        timeout=GIT_TIMEOUT,
    )
    if not result.stdout.strip():
        logger.info("Creating optimization branch %s in %s", branch, source_path)
        subprocess.run(
            ["git", "-C", str(source_path), "checkout", "-b", branch],
            check=True,
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT,
        )
    else:
        current = subprocess.run(
            ["git", "-C", str(source_path), "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT,
        )
        if current.stdout.strip() != branch:
            subprocess.run(
                ["git", "-C", str(source_path), "checkout", branch],
                check=True,
                capture_output=True,
                text=True,
                timeout=GIT_TIMEOUT,
            )


def get_diff_summary(source_path: Path) -> str:
    """Capture a short diff stat of the last commit vs its parent."""
    result = subprocess.run(
        ["git", "-C", str(source_path), "diff", "--stat", "HEAD~1", "HEAD"],
        capture_output=True,
        text=True,
        timeout=GIT_TIMEOUT,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def revert_last_change(source_path: Path) -> None:
    """Reset the DPDK submodule to the previous commit."""
    subprocess.run(
        ["git", "-C", str(source_path), "reset", "--hard", "HEAD~1"],
        check=True,
        capture_output=True,
        text=True,
        timeout=GIT_TIMEOUT,
    )
    logger.info("Reverted DPDK submodule to %s", git_submodule_head(source_path)[:12])


def force_push_source(source_path: Path, branch: str) -> None:
    """Force-push the DPDK submodule's optimization branch to its remote."""
    subprocess.run(
        ["git", "-C", str(source_path), "push", "--force", "origin", branch],
        check=True,
        capture_output=True,
        text=True,
        timeout=GIT_TIMEOUT,
    )
    logger.info("Force-pushed %s to origin in %s", branch, source_path)


def full_revert(source_path: Path, branch: str, dry_run: bool) -> str:
    """Revert the last DPDK submodule commit and force-push.

    Args:
        source_path: Path to the DPDK submodule.
        branch: Optimization branch name to force-push.
        dry_run: If True, skip push operations.

    Returns:
        The commit SHA that was reverted (before reset).
    """
    old_head = git_submodule_head(source_path)
    revert_last_change(source_path)
    if not dry_run and branch:
        force_push_source(source_path, branch)
    git_add_commit_push(
        [str(source_path)],
        "revert: manual revert of DPDK submodule",
        dry_run=dry_run,
    )
    return old_head


def record_result_or_revert(
    metric: float | None,
    best_val: float | None,
    direction: Direction,
    seq: int,
    commit: str,
    description: str,
    source_path: Path,
    dry_run: bool,
    results_path: Path,
    failures_path: Path,
    optimization_branch: str = "",
) -> bool:
    """Record a successful result or revert the change and record a failure.

    Args:
        optimization_branch: If set, force-push this branch in the submodule
            after reverting so the fork stays in sync.

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
        files = [str(results_path), str(source_path)]
        git_add_commit_push(files, f"results: iteration {seq:04d}", dry_run=dry_run)
    else:
        print(f"No improvement ({metric} vs best {best_val}). Reverting.")
        diff_summary = get_diff_summary(source_path)
        revert_last_change(source_path)
        if optimization_branch and not dry_run:
            force_push_source(source_path, optimization_branch)
        append_failure(commit, metric, description, diff_summary, path=failures_path)
        files = [str(results_path), str(failures_path), str(source_path)]
        git_add_commit_push(files, f"revert: iteration {seq:04d}", dry_run=dry_run)

    return improved
