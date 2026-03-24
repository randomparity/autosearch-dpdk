"""Agent-side protocol operations for creating and polling test requests."""

from __future__ import annotations

import logging
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from src.protocol.schema import TestRequest

logger = logging.getLogger(__name__)

DEFAULT_REQUESTS_DIR = Path("requests")


def next_sequence(requests_dir: Path | None = None) -> int:
    """Return the next sequence number based on existing request files.

    Scans the requests directory for JSON files, extracts the leading
    sequence number from filenames, and returns max + 1 (or 1 if empty).

    Args:
        requests_dir: Directory containing request JSON files.
    """
    directory = requests_dir or DEFAULT_REQUESTS_DIR
    max_seq = 0
    for path in directory.glob("*.json"):
        try:
            seq = int(path.stem.split("_")[0])
            max_seq = max(max_seq, seq)
        except (ValueError, IndexError):
            continue
    return max_seq + 1


def create_request(
    seq: int,
    commit: str,
    campaign: dict,
    description: str,
    requests_dir: Path | None = None,
) -> Path:
    """Create a new pending test request file.

    Args:
        seq: Sequence number for this iteration.
        commit: DPDK submodule commit SHA.
        campaign: Campaign configuration dict (must have 'metric' and 'dts' keys).
        description: Human-readable description of the change.
        requests_dir: Directory to write the request file into.

    Returns:
        Path to the newly created JSON file.
    """
    directory = requests_dir or DEFAULT_REQUESTS_DIR
    directory.mkdir(parents=True, exist_ok=True)

    metric = campaign["metric"]
    test_cfg = campaign.get("test", campaign.get("dts", {}))

    request = TestRequest(
        sequence=seq,
        created_at=datetime.now(timezone.utc).isoformat(),  # noqa: UP017
        dpdk_commit=commit,
        test_suites=test_cfg.get("test_suites", []),
        test_cases=test_cfg.get("test_cases"),
        perf=test_cfg.get("perf", True),
        metric_name=metric["name"],
        metric_path=metric["path"],
        description=description,
        backend=test_cfg.get("backend", "testpmd"),
    )

    path = directory / request.filename
    request.write(path)
    logger.info("Created request %04d at %s", seq, path)
    return path


def read_request(path: Path) -> TestRequest:
    """Read and deserialize a test request from a JSON file."""
    return TestRequest.read(path)


def find_latest_request(requests_dir: Path | None = None) -> TestRequest | None:
    """Find the most recent request file by sequence number.

    Args:
        requests_dir: Directory containing request JSON files.

    Returns:
        The TestRequest with the highest sequence number, or None if empty.
    """
    directory = requests_dir or DEFAULT_REQUESTS_DIR
    if not directory.is_dir():
        return None

    json_files = sorted(directory.glob("*.json"), reverse=True)
    for path in json_files:
        try:
            return TestRequest.read(path)
        except (ValueError, KeyError, TypeError) as exc:
            logger.warning("Skipping malformed request %s: %s", path.name, exc)
            continue

    return None


def poll_for_completion(
    seq: int,
    timeout: int = 3600,
    interval: int = 30,
    requests_dir: Path | None = None,
) -> TestRequest:
    """Poll git until the given request reaches a terminal state.

    Runs `git pull --rebase` at each interval and checks the request status.

    Args:
        seq: Sequence number to poll for.
        timeout: Maximum seconds to wait.
        interval: Seconds between polls.
        requests_dir: Directory containing request JSON files.

    Returns:
        The completed or failed TestRequest.

    Raises:
        TimeoutError: If the request does not complete within the timeout.
        FileNotFoundError: If the request file cannot be found.
    """
    directory = requests_dir or DEFAULT_REQUESTS_DIR
    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        subprocess.run(
            ["git", "pull", "--rebase"],
            capture_output=True,
            text=True,
        )

        matches = list(directory.glob(f"{seq:04d}_*.json"))
        if not matches:
            msg = f"Request file for sequence {seq} not found in {directory}"
            raise FileNotFoundError(msg)

        request = TestRequest.read(matches[0])
        if request.is_terminal:
            return request

        remaining = deadline - time.monotonic()
        logger.info(
            "Request %04d status=%s, %.0fs remaining",
            seq,
            request.status,
            remaining,
        )
        time.sleep(interval)

    msg = f"Request {seq} did not complete within {timeout}s"
    raise TimeoutError(msg)
