"""Runner-side protocol operations for claiming and updating test requests."""

from __future__ import annotations

import logging
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.protocol.schema import (
    STATUS_CLAIMED,
    STATUS_FAILED,
    STATUS_PENDING,
    TestRequest,
)

logger = logging.getLogger(__name__)

DEFAULT_REQUESTS_DIR = Path("requests")


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
    )
    subprocess.run(
        ["git", "commit", "-m", message],
        check=True,
        capture_output=True,
        text=True,
    )

    for attempt in range(retries):
        result = subprocess.run(
            ["git", "push"],
            capture_output=True,
            text=True,
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
            )
            if rebase.returncode != 0:
                logger.error("Pull --rebase failed: %s", rebase.stderr.strip())
                return False

    return False


def find_pending(requests_dir: Path | None = None) -> tuple[TestRequest, Path] | None:
    """Scan the requests directory for the oldest pending request.

    Args:
        requests_dir: Directory containing request JSON files.
            Defaults to 'requests/'.

    Returns:
        A tuple of (TestRequest, file_path) for the oldest pending request,
        or None if no pending requests exist.
    """
    directory = requests_dir or DEFAULT_REQUESTS_DIR
    if not directory.is_dir():
        return None

    json_files = sorted(directory.glob("*.json"))
    for path in json_files:
        try:
            request = TestRequest.read(path)
        except (ValueError, KeyError, TypeError) as exc:
            logger.warning("Skipping malformed request %s: %s", path.name, exc)
            continue
        if request.status == STATUS_PENDING:
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
    request.claimed_at = datetime.now(timezone.utc).isoformat()
    request.write(request_path)

    return _git_commit_push(
        request_path,
        f"runner: claim request {request.sequence:04d}",
    )


def update_status(
    request: TestRequest,
    status: str,
    request_path: Path,
    **fields: Any,
) -> bool:
    """Update a request's status and any extra fields, then commit and push.

    Args:
        request: The test request to update.
        status: The new status string.
        request_path: Path to the request JSON file.
        **fields: Additional fields to set on the request (e.g. results_json).

    Returns:
        True if the push succeeded, False otherwise.
    """
    request.transition_to(status)
    for key, value in fields.items():
        if not hasattr(request, key):
            msg = f"TestRequest has no field {key!r}"
            raise AttributeError(msg)
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
    return pushed


def fail(
    request: TestRequest,
    request_path: Path,
    error: str,
    log_snippet: str | None = None,
) -> None:
    """Mark a request as failed with an error message.

    Args:
        request: The test request to fail.
        request_path: Path to the request JSON file.
        error: Human-readable error description.
        log_snippet: Optional truncated build/test log.
    """
    extra: dict[str, Any] = {"error": error}
    if log_snippet is not None:
        extra["build_log_snippet"] = log_snippet
    extra["completed_at"] = datetime.now(timezone.utc).isoformat()

    pushed = update_status(request, STATUS_FAILED, request_path, **extra)
    if not pushed:
        logger.critical(
            "Could not push failure status for request %04d; local file written at %s",
            request.sequence,
            request_path,
        )
