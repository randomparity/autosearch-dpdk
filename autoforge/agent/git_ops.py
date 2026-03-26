"""Git operations for the agent optimization loop."""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from autoforge.agent.history import append_failure
from autoforge.agent.metric import compare_metric
from autoforge.protocol import GIT_TIMEOUT, Direction

logger = logging.getLogger(__name__)


@dataclass
class ResultContext:
    """Bundles request metadata for result recording."""

    seq: int
    commit: str
    description: str
    source_path: Path
    results_path: Path
    failures_path: Path
    optimization_branch: str = field(default="")


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
    """Stage files, commit, and optionally push.

    Raises:
        subprocess.CalledProcessError: If any git command fails.
    """
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


def capture_diff_summary(source_path: Path) -> str:
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


def _record_improvement(
    metric: float | None,
    best_val: float | None,
    ctx: ResultContext,
    dry_run: bool,
) -> None:
    """Log and commit an improved result."""
    if best_val is not None:
        logger.info("Improvement! %s -> %s", best_val, metric)
    else:
        logger.info("Baseline: %s", metric)
    files = [str(ctx.results_path), str(ctx.source_path)]
    git_add_commit_push(files, f"results: iteration {ctx.seq:04d}", dry_run=dry_run)


def _revert_and_record_failure(
    metric: float | None,
    best_val: float | None,
    ctx: ResultContext,
    dry_run: bool,
) -> None:
    """Revert the submodule, record the failure, and commit."""
    logger.info("No improvement (%s vs best %s). Reverting.", metric, best_val)
    diff_summary = capture_diff_summary(ctx.source_path)
    revert_last_change(ctx.source_path)
    if ctx.optimization_branch and not dry_run:
        force_push_source(ctx.source_path, ctx.optimization_branch)
    append_failure(ctx.commit, metric, ctx.description, diff_summary, path=ctx.failures_path)
    files = [str(ctx.results_path), str(ctx.failures_path), str(ctx.source_path)]
    git_add_commit_push(files, f"revert: iteration {ctx.seq:04d}", dry_run=dry_run)


def record_result_or_revert(
    metric: float | None,
    best_val: float | None,
    direction: Direction,
    ctx: ResultContext,
    dry_run: bool = False,
) -> bool:
    """Compare metric against best, record to TSV, and manage submodule state.

    If the metric is an improvement (or no baseline exists), commits the
    result and submodule pointer. Otherwise, reverts the last submodule
    commit, force-pushes the optimization branch if set, records the
    failure to the failures TSV, and commits the revert.

    Returns True if the result was an improvement.
    """
    improved = best_val is None or (
        metric is not None and compare_metric(metric, best_val, direction)
    )

    if improved:
        _record_improvement(metric, best_val, ctx, dry_run)
    else:
        _revert_and_record_failure(metric, best_val, ctx, dry_run)

    return improved
