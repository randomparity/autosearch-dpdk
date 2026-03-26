"""Tests for runner-side protocol operations."""

from __future__ import annotations

from autoforge.agent.protocol import create_request
from autoforge.protocol import STATUS_CLAIMED, STATUS_PENDING
from autoforge.runner.protocol import find_by_status

SAMPLE_CAMPAIGN = {
    "metric": {
        "name": "throughput_mpps",
        "path": "results.throughput_mpps",
    },
    "project": {
        "build": "local",
        "deploy": "local",
        "test": "testpmd-memif",
    },
}


class TestFindByStatus:
    def test_returns_none_for_empty_dir(self, tmp_path) -> None:
        assert find_by_status(tmp_path, STATUS_PENDING) is None

    def test_returns_none_for_nonexistent_dir(self, tmp_path) -> None:
        assert find_by_status(tmp_path / "nonexistent", STATUS_PENDING) is None

    def test_finds_pending_request(self, tmp_path) -> None:
        create_request(1, "abc123", SAMPLE_CAMPAIGN, "test", tmp_path)
        result = find_by_status(tmp_path, STATUS_PENDING)
        assert result is not None
        request, path = result
        assert request.sequence == 1
        assert request.status == STATUS_PENDING

    def test_skips_claimed_request(self, tmp_path) -> None:
        path = create_request(1, "abc123", SAMPLE_CAMPAIGN, "test", tmp_path)
        from autoforge.protocol import TestRequest

        req = TestRequest.read(path)
        req.status = STATUS_CLAIMED
        req.write(path)

        assert find_by_status(tmp_path, STATUS_PENDING) is None

    def test_returns_oldest_pending(self, tmp_path) -> None:
        create_request(2, "bbb", SAMPLE_CAMPAIGN, "second", tmp_path)
        create_request(1, "aaa", SAMPLE_CAMPAIGN, "first", tmp_path)
        result = find_by_status(tmp_path, STATUS_PENDING)
        assert result is not None
        request, _ = result
        assert request.sequence == 1
