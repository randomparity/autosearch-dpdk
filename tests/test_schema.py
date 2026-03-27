"""Tests for protocol schema serialization and validation."""

from __future__ import annotations

import json

import pytest

from autoforge.protocol import (
    STATUS_BUILDING,
    STATUS_BUILT,
    STATUS_CLAIMED,
    STATUS_COMPLETED,
    STATUS_DEPLOYED,
    STATUS_DEPLOYING,
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
        "source_commit": "abc123def456",
        "description": "Increase burst size in testpmd",
        "build_plugin": "local",
        "deploy_plugin": "local",
        "test_plugin": "testpmd-memif",
        "metric_name": "throughput_mpps",
        "metric_path": "throughput_mpps",
    }
    defaults.update(overrides)
    return TestRequest(**defaults)


class TestSerialization:
    def test_round_trip_json(self) -> None:
        req = make_request()
        raw = req.to_json()
        restored = TestRequest.from_json(raw)
        assert restored.sequence == req.sequence
        assert restored.source_commit == req.source_commit
        assert restored.build_plugin == "local"
        assert restored.deploy_plugin == "local"
        assert restored.test_plugin == "testpmd-memif"
        assert restored.status == STATUS_PENDING

    def test_round_trip_preserves_all_fields(self) -> None:
        req = make_request(
            status=STATUS_COMPLETED,
            claimed_at="2025-01-15T10:31:00",
            built_at="2025-01-15T10:35:00",
            deployed_at="2025-01-15T10:36:00",
            completed_at="2025-01-15T11:00:00",
            metric_value=14.5,
            results_json={"throughput": 14.5},
            results_summary="All tests passed",
            build_runner_id="build-01",
            deploy_runner_id="deploy-01",
            test_runner_id="test-01",
        )
        restored = TestRequest.from_json(req.to_json())
        assert restored.claimed_at == "2025-01-15T10:31:00"
        assert restored.built_at == "2025-01-15T10:35:00"
        assert restored.deployed_at == "2025-01-15T10:36:00"
        assert restored.completed_at == "2025-01-15T11:00:00"
        assert restored.metric_value == 14.5
        assert restored.results_json == {"throughput": 14.5}
        assert restored.results_summary == "All tests passed"
        assert restored.build_runner_id == "build-01"

    def test_to_json_is_valid_json(self) -> None:
        req = make_request()
        parsed = json.loads(req.to_json())
        assert parsed["sequence"] == 1
        assert parsed["status"] == "pending"
        assert parsed["build_plugin"] == "local"

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

    def test_profile_plugin_optional(self) -> None:
        req = make_request()
        assert req.profile_plugin == ""
        req2 = make_request(profile_plugin="perf-record")
        assert req2.profile_plugin == "perf-record"


class TestTags:
    def test_tags_default_none(self) -> None:
        req = make_request()
        assert req.tags is None

    def test_tags_round_trip(self) -> None:
        req = make_request(tags=["memcpy", "cache"])
        restored = TestRequest.from_json(req.to_json())
        assert restored.tags == ["memcpy", "cache"]

    def test_tags_empty_list(self) -> None:
        req = make_request(tags=[])
        restored = TestRequest.from_json(req.to_json())
        assert restored.tags == []

    def test_tags_in_json_output(self) -> None:
        import json

        req = make_request(tags=["batching"])
        parsed = json.loads(req.to_json())
        assert parsed["tags"] == ["batching"]


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
            STATUS_BUILT,
            STATUS_DEPLOYING,
            STATUS_DEPLOYED,
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
            (STATUS_BUILDING, STATUS_BUILT),
            (STATUS_BUILT, STATUS_DEPLOYING),
            (STATUS_DEPLOYING, STATUS_DEPLOYED),
            (STATUS_DEPLOYED, STATUS_RUNNING),
            (STATUS_RUNNING, STATUS_COMPLETED),
        ]
        for current, target in transitions:
            validate_transition(current, target)

    def test_any_non_terminal_state_can_fail(self) -> None:
        for status in [
            STATUS_PENDING,
            STATUS_CLAIMED,
            STATUS_BUILDING,
            STATUS_BUILT,
            STATUS_DEPLOYING,
            STATUS_DEPLOYED,
            STATUS_RUNNING,
        ]:
            validate_transition(status, STATUS_FAILED)

    def test_cannot_transition_backwards(self) -> None:
        with pytest.raises(ValueError, match="Cannot transition"):
            validate_transition(STATUS_CLAIMED, STATUS_PENDING)

    def test_cannot_skip_states(self) -> None:
        with pytest.raises(ValueError, match="Cannot transition"):
            validate_transition(STATUS_BUILDING, STATUS_DEPLOYED)

    def test_terminal_states_cannot_transition(self) -> None:
        for target in [
            STATUS_PENDING,
            STATUS_CLAIMED,
            STATUS_BUILDING,
            STATUS_RUNNING,
        ]:
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
        req.transition_to(STATUS_BUILT)
        assert req.status == STATUS_BUILT

    def test_transition_to_invalid_raises(self) -> None:
        req = make_request()
        with pytest.raises(ValueError):
            req.transition_to(STATUS_COMPLETED)


class TestNewFields:
    def test_deploy_log_snippet_default_none(self) -> None:
        req = make_request()
        assert req.deploy_log_snippet is None

    def test_test_log_snippet_default_none(self) -> None:
        req = make_request()
        assert req.test_log_snippet is None

    def test_failed_phase_default_none(self) -> None:
        req = make_request()
        assert req.failed_phase is None

    def test_new_fields_round_trip(self) -> None:
        req = make_request(
            status=STATUS_FAILED,
            deploy_log_snippet="deploy error log",
            test_log_snippet="test error log",
            failed_phase="deploy",
        )
        restored = TestRequest.from_json(req.to_json())
        assert restored.deploy_log_snippet == "deploy error log"
        assert restored.test_log_snippet == "test error log"
        assert restored.failed_phase == "deploy"

    def test_backward_compat_old_json_missing_new_fields(self) -> None:
        """Old JSONs missing new fields still deserialize (defaults apply)."""
        req = make_request()
        data = json.loads(req.to_json())
        data.pop("deploy_log_snippet", None)
        data.pop("test_log_snippet", None)
        data.pop("failed_phase", None)
        restored = TestRequest(**data)
        assert restored.deploy_log_snippet is None
        assert restored.test_log_snippet is None
        assert restored.failed_phase is None


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

    def test_intermediate_states_not_terminal(self) -> None:
        for status in [STATUS_BUILT, STATUS_DEPLOYING, STATUS_DEPLOYED]:
            req = make_request(status=status)
            assert req.is_terminal is False
