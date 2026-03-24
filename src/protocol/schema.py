"""Shared protocol schema for agent-runner communication."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

STATUS_PENDING = "pending"
STATUS_CLAIMED = "claimed"
STATUS_BUILDING = "building"
STATUS_RUNNING = "running"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"

VALID_STATUSES = frozenset({
    STATUS_PENDING,
    STATUS_CLAIMED,
    STATUS_BUILDING,
    STATUS_RUNNING,
    STATUS_COMPLETED,
    STATUS_FAILED,
})

VALID_TRANSITIONS: dict[str, frozenset[str]] = {
    STATUS_PENDING: frozenset({STATUS_CLAIMED, STATUS_FAILED}),
    STATUS_CLAIMED: frozenset({STATUS_BUILDING, STATUS_FAILED}),
    STATUS_BUILDING: frozenset({STATUS_RUNNING, STATUS_FAILED}),
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
    dpdk_commit: str
    test_suites: list[str]
    test_cases: list[str] | None
    perf: bool
    metric_name: str
    metric_path: str
    description: str
    backend: str = "testpmd"
    status: str = STATUS_PENDING

    claimed_at: str | None = None
    completed_at: str | None = None
    build_log_snippet: str | None = None
    results_json: dict[str, Any] | None = None
    results_summary: str | None = None
    metric_value: float | None = None
    error: str | None = None

    def __post_init__(self) -> None:
        validate_status(self.status)

    def transition_to(self, new_status: str) -> None:
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


def validate_status(status: str) -> None:
    """Raise ValueError if status is not a valid status string."""
    if status not in VALID_STATUSES:
        msg = f"Invalid status {status!r}, must be one of {sorted(VALID_STATUSES)}"
        raise ValueError(msg)


def validate_transition(current: str, new: str) -> None:
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
