"""Shared protocol schema for agent-runner communication."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

StatusLiteral = Literal[
    "pending",
    "claimed",
    "building",
    "built",
    "deploying",
    "deployed",
    "running",
    "completed",
    "failed",
]

STATUS_PENDING: StatusLiteral = "pending"
STATUS_CLAIMED: StatusLiteral = "claimed"
STATUS_BUILDING: StatusLiteral = "building"
STATUS_BUILT: StatusLiteral = "built"
STATUS_DEPLOYING: StatusLiteral = "deploying"
STATUS_DEPLOYED: StatusLiteral = "deployed"
STATUS_RUNNING: StatusLiteral = "running"
STATUS_COMPLETED: StatusLiteral = "completed"
STATUS_FAILED: StatusLiteral = "failed"

VALID_STATUSES = frozenset(
    {
        STATUS_PENDING,
        STATUS_CLAIMED,
        STATUS_BUILDING,
        STATUS_BUILT,
        STATUS_DEPLOYING,
        STATUS_DEPLOYED,
        STATUS_RUNNING,
        STATUS_COMPLETED,
        STATUS_FAILED,
    }
)

VALID_TRANSITIONS: dict[str, frozenset[str]] = {
    STATUS_PENDING: frozenset({STATUS_CLAIMED, STATUS_FAILED}),
    STATUS_CLAIMED: frozenset({STATUS_BUILDING, STATUS_FAILED}),
    STATUS_BUILDING: frozenset({STATUS_BUILT, STATUS_FAILED}),
    STATUS_BUILT: frozenset({STATUS_DEPLOYING, STATUS_FAILED}),
    STATUS_DEPLOYING: frozenset({STATUS_DEPLOYED, STATUS_FAILED}),
    STATUS_DEPLOYED: frozenset({STATUS_RUNNING, STATUS_FAILED}),
    STATUS_RUNNING: frozenset({STATUS_COMPLETED, STATUS_FAILED}),
    STATUS_COMPLETED: frozenset(),
    STATUS_FAILED: frozenset(),
}


@dataclass
class TestRequest:
    """A single test iteration request exchanged between agent and runner."""

    __test__ = False  # Prevent pytest from collecting this as a test class

    sequence: int
    created_at: str
    source_commit: str
    description: str

    # Independent plugin selection
    build_plugin: str
    deploy_plugin: str
    test_plugin: str
    profile_plugin: str = ""

    # Experiment metadata
    tags: list[str] | None = None

    # Test spec
    metric_name: str = ""
    metric_path: str = ""

    # Status
    status: StatusLiteral = STATUS_PENDING
    claimed_at: str | None = None
    built_at: str | None = None
    deployed_at: str | None = None
    completed_at: str | None = None

    # Runner tracking
    build_runner_id: str | None = None
    deploy_runner_id: str | None = None
    test_runner_id: str | None = None

    # Results
    build_log_snippet: str | None = None
    results_json: dict[str, Any] | None = None
    results_summary: str | None = None
    metric_value: float | None = None
    error: str | None = None

    def __post_init__(self) -> None:
        validate_status(self.status)

    def transition_to(self, new_status: StatusLiteral) -> None:
        """Transition to a new status, raising ValueError on invalid transitions."""
        validate_transition(self.status, new_status)
        self.status = new_status

    def to_json(self) -> str:
        """Serialize to a JSON string."""
        return json.dumps(asdict(self), indent=2)

    def write(self, path: Path) -> None:
        """Write this request to a JSON file."""
        path.write_text(self.to_json() + "\n")

    @classmethod
    def from_json(cls, raw: str) -> TestRequest:
        """Deserialize from a JSON string."""
        data = json.loads(raw)
        return cls(**data)

    @classmethod
    def read(cls, path: Path) -> TestRequest:
        """Read a request from a JSON file."""
        return cls.from_json(path.read_text())

    @property
    def filename(self) -> str:
        """Generate the canonical filename for this request."""
        date_part = self.created_at.replace(":", "-").replace("T", "_")
        return f"{self.sequence:04d}_{date_part}.json"

    @property
    def is_terminal(self) -> bool:
        """Whether this request is in a terminal state."""
        return self.status in (STATUS_COMPLETED, STATUS_FAILED)


def validate_status(status: StatusLiteral) -> None:
    """Raise ValueError if status is not a valid status string."""
    if status not in VALID_STATUSES:
        msg = f"Invalid status {status!r}, must be one of {sorted(VALID_STATUSES)}"
        raise ValueError(msg)


def validate_transition(current: StatusLiteral, new: StatusLiteral) -> None:
    """Raise ValueError if the status transition is not allowed."""
    validate_status(current)
    validate_status(new)
    allowed = VALID_TRANSITIONS[current]
    if new not in allowed:
        msg = f"Cannot transition from {current!r} to {new!r} (allowed: {sorted(allowed)})"
        raise ValueError(msg)


def request_fields() -> list[str]:
    """Return the list of field names for TestRequest."""
    return [f.name for f in TestRequest.__dataclass_fields__.values()]


def extract_metric(data: dict, path: str) -> float:
    """Walk a dot-notation path into nested dicts/lists and return the value.

    Numeric path components are treated as list indices.

    Args:
        data: The root dictionary (e.g. DTS results JSON).
        path: Dot-separated key path (e.g. 'test_runs.0.throughput_mpps').

    Returns:
        The numeric value at the given path.

    Raises:
        KeyError: If a dict key is missing.
        IndexError: If a list index is out of range.
        ValueError: If the path is empty or the value is not numeric.
    """
    if not path:
        msg = "Metric path must not be empty"
        raise ValueError(msg)

    current: object = data
    for key in path.split("."):
        if isinstance(current, list):
            current = current[int(key)]
        elif isinstance(current, dict):
            current = current[key]
        else:
            msg = f"Cannot index into {type(current).__name__} with key {key!r}"
            raise KeyError(msg)

    try:
        return float(current)  # type: ignore[arg-type]  # validated by isinstance chain
    except (TypeError, ValueError) as exc:
        msg = f"Metric value at '{path}' is not numeric: {current!r}"
        raise ValueError(msg) from exc
