"""Runner-side protocol operations for claiming and updating test requests."""

from __future__ import annotations

import logging
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from autoforge.protocol import (
    GIT_TIMEOUT,
    STATUS_CLAIMED,
    STATUS_FAILED,
    StatusLiteral,
    TestRequest,
)

logger = logging.getLogger(__name__)


def _git_commit_push(path: Path, message: str, retries: int = 3) -> bool:
    """Stage a file, commit, and push. Retries with pull --rebase on conflict.

    Args:
        path: File to stage and commit.
        message: Commit message.
        retries: Maximum number of push attempts.

    Returns:
        True if the push succeeded, False if all retries failed.
    """
    subprocess.run(
        ["git", "add", str(path)],
        check=True,
        capture_output=True,
        text=True,
        timeout=GIT_TIMEOUT,
    )
    subprocess.run(
        ["git", "commit", "-m", message],
        check=True,
        capture_output=True,
        text=True,
        timeout=GIT_TIMEOUT,
    )

    for attempt in range(retries):
        result = subprocess.run(
            ["git", "push"],
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT,
        )
        if result.returncode == 0:
            return True

        logger.warning(
            "Push failed (attempt %d/%d): %s", attempt + 1, retries, result.stderr.strip()
        )
        if attempt < retries - 1:
            rebase = subprocess.run(
                ["git", "pull", "--rebase"],
                capture_output=True,
                text=True,
                timeout=GIT_TIMEOUT,
            )
            if rebase.returncode != 0:
                logger.error("Pull --rebase failed: %s", rebase.stderr.strip())
                return False

    return False


def find_by_status(requests_dir: Path, status: StatusLiteral) -> tuple[TestRequest, Path] | None:
    """Scan the requests directory for the oldest request with a given status.

    Args:
        requests_dir: Directory to scan for JSON request files.
        status: The status to match.

    Returns:
        A tuple of (TestRequest, file_path) for the oldest matching request,
        or None if none found.
    """
    if not requests_dir.is_dir():
        return None

    json_files = sorted(requests_dir.glob("*.json"))
    for path in json_files:
        try:
            request = TestRequest.read(path)
        except (ValueError, KeyError, TypeError, OSError) as exc:
            logger.warning("Skipping malformed request %s: %s", path.name, exc)
            continue
        if request.status == status:
            return (request, path)

    return None


def claim(request: TestRequest, request_path: Path) -> bool:
    """Atomically claim a pending request.

    Sets status to claimed, records claimed_at timestamp, writes the file,
    and commits+pushes to git. Retries on push conflicts.

    Args:
        request: The test request to claim.
        request_path: Path to the request JSON file.

    Returns:
        True if the claim succeeded, False otherwise.
    """
    request.transition_to(STATUS_CLAIMED)
    request.claimed_at = datetime.now(UTC).isoformat()
    request.write(request_path)

    pushed = _git_commit_push(
        request_path,
        f"runner: claim request {request.sequence:04d}",
    )
    if pushed:
        logger.info("Claim pushed for request %04d", request.sequence)
    else:
        logger.error("Claim push failed for request %04d", request.sequence)
    return pushed


def update_status(
    request: TestRequest,
    status: StatusLiteral,
    request_path: Path,
    *,
    results_json: dict | None = None,
    results_summary: str | None = None,
    metric_value: float | None = None,
    completed_at: str | None = None,
    error: str | None = None,
    build_log_snippet: str | None = None,
) -> bool:
    """Update a request's status and result fields, then commit and push.

    Args:
        request: The test request to update.
        status: The new status string.
        request_path: Path to the request JSON file.
        results_json: Test results as a dict.
        results_summary: Human-readable results summary.
        metric_value: Extracted metric value.
        completed_at: ISO timestamp of completion.
        error: Error description (for failed requests).
        build_log_snippet: Truncated build log (for failed builds).

    Returns:
        True if the push succeeded, False otherwise.
    """
    logger.info("Transitioning request %04d: %s -> %s", request.sequence, request.status, status)
    request.transition_to(status)
    fields = {
        "results_json": results_json,
        "results_summary": results_summary,
        "metric_value": metric_value,
        "completed_at": completed_at,
        "error": error,
        "build_log_snippet": build_log_snippet,
    }
    for key, value in fields.items():
        if value is not None:
            setattr(request, key, value)

    request.write(request_path)
    pushed = _git_commit_push(
        request_path,
        f"runner: {status} request {request.sequence:04d}",
    )
    if not pushed:
        logger.error(
            "Failed to push status update to %s for request %04d", status, request.sequence
        )
    else:
        logger.debug("Pushed status %s for request %04d", status, request.sequence)
    return pushed


def fail(
    request: TestRequest,
    request_path: Path,
    error: str,
    log_snippet: str | None = None,
) -> bool:
    """Mark a request as failed with an error message.

    Args:
        request: The test request to fail.
        request_path: Path to the request JSON file.
        error: Human-readable error description.
        log_snippet: Optional truncated build/test log.

    Returns:
        True if the failure status was successfully pushed, False if only
        written locally.
    """
    pushed = update_status(
        request,
        STATUS_FAILED,
        request_path,
        error=error,
        build_log_snippet=log_snippet,
        completed_at=datetime.now(UTC).isoformat(),
    )
    if not pushed:
        logger.critical(
            "Could not push failure status for request %04d; local file written at %s",
            request.sequence,
            request_path,
        )
    return pushed
