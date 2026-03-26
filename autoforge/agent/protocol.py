"""Agent-side protocol operations for creating and polling test requests."""

from __future__ import annotations

import logging
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

from autoforge.campaign import CampaignConfig, metric_config, project_config
from autoforge.protocol import GIT_TIMEOUT, TestRequest

logger = logging.getLogger(__name__)


def next_sequence(requests_dir: Path) -> int:
    """Return the next sequence number based on existing request files.

    Args:
        requests_dir: Directory containing request JSON files.
    """
    max_seq = 0
    if requests_dir.is_dir():
        for path in requests_dir.glob("*.json"):
            try:
                seq = int(path.stem.split("_")[0])
                max_seq = max(max_seq, seq)
            except (ValueError, IndexError):
                continue
    return max_seq + 1


def create_request(
    seq: int,
    commit: str,
    campaign: CampaignConfig,
    description: str,
    requests_dir: Path,
    *,
    skip_profiling: bool = False,
    tags: list[str] | None = None,
) -> Path:
    """Create a new pending test request file.

    Args:
        seq: Sequence number for this iteration.
        commit: DPDK submodule commit SHA.
        campaign: Campaign configuration.
        description: Human-readable description of the change.
        requests_dir: Directory to write the request file into.
        skip_profiling: If True, omit the profiler plugin from the request.
        tags: Optional experiment category tags.

    Returns:
        Path to the newly created JSON file.
    """
    requests_dir.mkdir(parents=True, exist_ok=True)

    mc = metric_config(campaign)
    pc = project_config(campaign)

    profiler = "" if skip_profiling else pc.get("profiler", "")

    request = TestRequest(
        sequence=seq,
        created_at=datetime.now(UTC).isoformat(),
        source_commit=commit,
        description=description,
        build_plugin=pc.get("build", ""),
        deploy_plugin=pc.get("deploy", ""),
        test_plugin=pc.get("test", ""),
        profile_plugin=profiler,
        tags=tags,
        metric_name=mc.get("name", ""),
        metric_path=mc.get("path", ""),
    )

    path = requests_dir / request.filename
    request.write(path)
    logger.info("Created request %04d at %s", seq, path)
    return path


def find_latest_request(requests_dir: Path) -> TestRequest | None:
    """Find the most recent request file by sequence number.

    Args:
        requests_dir: Directory containing request JSON files.

    Returns:
        The TestRequest with the highest sequence number, or None if empty.
    """
    if not requests_dir.is_dir():
        return None

    json_files = sorted(requests_dir.glob("*.json"), reverse=True)
    for path in json_files:
        try:
            return TestRequest.read(path)
        except (ValueError, KeyError, TypeError) as exc:
            logger.warning("Skipping malformed request %s: %s", path.name, exc)

    return None


def find_request_by_seq(seq: int, requests_dir: Path) -> TestRequest | None:
    """Find a request file by its sequence number.

    Args:
        seq: Sequence number to look up.
        requests_dir: Directory containing request JSON files.

    Returns:
        The TestRequest if found, or None.
    """
    matches = list(requests_dir.glob(f"{seq:04d}_*.json"))
    if not matches:
        return None
    try:
        return TestRequest.read(matches[0])
    except (ValueError, KeyError, TypeError) as exc:
        logger.warning("Malformed request file %s: %s", matches[0].name, exc)
        return None


def poll_for_completion(
    seq: int,
    timeout: int = 3600,
    interval: int = 30,
    *,
    requests_dir: Path,
) -> TestRequest:
    """Poll git until the given request reaches a terminal state.

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

    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        pull_result = subprocess.run(
            ["git", "pull", "--rebase"],
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT,
        )
        if pull_result.returncode != 0:
            logger.warning("git pull --rebase failed: %s", pull_result.stderr.strip())

        matches = list(requests_dir.glob(f"{seq:04d}_*.json"))
        if not matches:
            msg = f"Request file for sequence {seq} not found in {requests_dir}"
            raise FileNotFoundError(msg)

        try:
            request = TestRequest.read(matches[0])
        except (ValueError, KeyError, TypeError, OSError) as exc:
            logger.warning("Error reading request %04d, retrying: %s", seq, exc)
            time.sleep(interval)
            continue
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
