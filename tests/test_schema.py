"""Tests for protocol schema serialization and validation."""

from __future__ import annotations

import json

import pytest

from src.protocol.schema import (
    STATUS_BUILDING,
    STATUS_CLAIMED,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_RUNNING,
    TestRequest,
    validate_status,
    validate_transition,
)


def make_request(**overrides: object) -> TestRequest:
    """Create a TestRequest with sensible defaults, overridden by kwargs."""
    defaults = {
        "sequence": 1,
        "created_at": "2025-01-15T10:30:00",
        "dpdk_commit": "abc123def456",
        "test_suites": ["TestPmd"],
        "test_cases": None,
        "perf": True,
        "metric_name": "throughput_mpps",
        "metric_path": "test_runs.0.test_suites.0.test_cases.0.throughput_mpps",
        "description": "Increase burst size in testpmd",
    }
    defaults.update(overrides)
    return TestRequest(**defaults)


class TestSerialization:
    def test_round_trip_json(self) -> None:
        req = make_request()
        raw = req.to_json()
        restored = TestRequest.from_json(raw)
        assert restored.sequence == req.sequence
        assert restored.dpdk_commit == req.dpdk_commit
        assert restored.test_suites == req.test_suites
        assert restored.status == STATUS_PENDING

    def test_round_trip_preserves_all_fields(self) -> None:
        req = make_request(
            status=STATUS_COMPLETED,
            claimed_at="2025-01-15T10:31:00",
            completed_at="2025-01-15T11:00:00",
            metric_value=14.5,
            results_json={"throughput": 14.5},
            results_summary="All tests passed",
        )
        restored = TestRequest.from_json(req.to_json())
        assert restored.claimed_at == "2025-01-15T10:31:00"
        assert restored.completed_at == "2025-01-15T11:00:00"
        assert restored.metric_value == 14.5
        assert restored.results_json == {"throughput": 14.5}
        assert restored.results_summary == "All tests passed"

    def test_to_json_is_valid_json(self) -> None:
        req = make_request()
        parsed = json.loads(req.to_json())
        assert parsed["sequence"] == 1
        assert parsed["status"] == "pending"

    def test_from_json_with_unknown_fields_raises(self) -> None:
        req = make_request()
        data = json.loads(req.to_json())
        data["extra_field"] = "unexpected"
        with pytest.raises(TypeError):
            TestRequest(**data)

    def test_write_and_read_file(self, tmp_path) -> None:
        req = make_request()
        path = tmp_path / "test.json"
        req.write(path)
        restored = TestRequest.read(path)
        assert restored.sequence == req.sequence
        assert restored.status == STATUS_PENDING


class TestFilename:
    def test_filename_format(self) -> None:
        req = make_request(sequence=1, created_at="2025-01-15T10:30:00")
        assert req.filename == "0001_2025-01-15_10-30-00.json"

    def test_filename_zero_padded(self) -> None:
        req = make_request(sequence=42)
        assert req.filename.startswith("0042_")

    def test_filename_large_sequence(self) -> None:
        req = make_request(sequence=9999)
        assert req.filename.startswith("9999_")


class TestStatusValidation:
    def test_valid_statuses(self) -> None:
        for status in [
            STATUS_PENDING,
            STATUS_CLAIMED,
            STATUS_BUILDING,
            STATUS_RUNNING,
            STATUS_COMPLETED,
            STATUS_FAILED,
        ]:
            validate_status(status)

    def test_invalid_status_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid status"):
            validate_status("invalid")

    def test_request_with_invalid_status_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid status"):
            make_request(status="bogus")


class TestStateTransitions:
    def test_valid_forward_transitions(self) -> None:
        transitions = [
            (STATUS_PENDING, STATUS_CLAIMED),
            (STATUS_CLAIMED, STATUS_BUILDING),
            (STATUS_BUILDING, STATUS_RUNNING),
            (STATUS_RUNNING, STATUS_COMPLETED),
        ]
        for current, target in transitions:
            validate_transition(current, target)

    def test_any_state_can_fail(self) -> None:
        for status in [STATUS_PENDING, STATUS_CLAIMED, STATUS_BUILDING, STATUS_RUNNING]:
            validate_transition(status, STATUS_FAILED)

    def test_cannot_transition_backwards(self) -> None:
        with pytest.raises(ValueError, match="Cannot transition"):
            validate_transition(STATUS_CLAIMED, STATUS_PENDING)

    def test_terminal_states_cannot_transition(self) -> None:
        for target in [STATUS_PENDING, STATUS_CLAIMED, STATUS_BUILDING, STATUS_RUNNING]:
            with pytest.raises(ValueError, match="Cannot transition"):
                validate_transition(STATUS_COMPLETED, target)
            with pytest.raises(ValueError, match="Cannot transition"):
                validate_transition(STATUS_FAILED, target)

    def test_transition_to_method(self) -> None:
        req = make_request()
        req.transition_to(STATUS_CLAIMED)
        assert req.status == STATUS_CLAIMED
        req.transition_to(STATUS_BUILDING)
        assert req.status == STATUS_BUILDING

    def test_transition_to_invalid_raises(self) -> None:
        req = make_request()
        with pytest.raises(ValueError):
            req.transition_to(STATUS_COMPLETED)


class TestIsTerminal:
    def test_completed_is_terminal(self) -> None:
        req = make_request(status=STATUS_COMPLETED)
        assert req.is_terminal is True

    def test_failed_is_terminal(self) -> None:
        req = make_request(status=STATUS_FAILED)
        assert req.is_terminal is True

    def test_pending_is_not_terminal(self) -> None:
        req = make_request()
        assert req.is_terminal is False

    def test_running_is_not_terminal(self) -> None:
        req = make_request(status=STATUS_RUNNING)
        assert req.is_terminal is False
