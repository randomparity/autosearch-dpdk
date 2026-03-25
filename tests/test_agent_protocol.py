"""Tests for agent-side protocol operations."""

from __future__ import annotations

import json

from autoforge.agent.protocol import create_request, find_latest_request, next_sequence
from autoforge.protocol import STATUS_PENDING, TestRequest

SAMPLE_CAMPAIGN = {
    "metric": {
        "name": "throughput_mpps",
        "path": "throughput_mpps",
    },
    "project": {
        "build": "local-server",
        "deploy": "local",
        "test": "testpmd-memif",
    },
}


class TestNextSequence:
    def test_empty_dir_returns_1(self, tmp_path) -> None:
        assert next_sequence(requests_dir=tmp_path) == 1

    def test_increments_from_existing(self, tmp_path) -> None:
        (tmp_path / "0001_2025-01-15_10-30-00.json").write_text("{}")
        (tmp_path / "0003_2025-01-15_11-30-00.json").write_text("{}")
        assert next_sequence(requests_dir=tmp_path) == 4

    def test_ignores_non_json_files(self, tmp_path) -> None:
        (tmp_path / ".gitkeep").touch()
        (tmp_path / "notes.txt").write_text("hello")
        assert next_sequence(requests_dir=tmp_path) == 1


class TestCreateRequest:
    def test_creates_json_file(self, tmp_path) -> None:
        path = create_request(
            seq=1,
            commit="abc123",
            campaign=SAMPLE_CAMPAIGN,
            description="Test change",
            requests_dir=tmp_path,
        )
        assert path.exists()
        assert path.suffix == ".json"

    def test_file_contains_valid_request(self, tmp_path) -> None:
        path = create_request(
            seq=1,
            commit="abc123",
            campaign=SAMPLE_CAMPAIGN,
            description="Test change",
            requests_dir=tmp_path,
        )
        data = json.loads(path.read_text())
        assert data["sequence"] == 1
        assert data["source_commit"] == "abc123"
        assert data["status"] == STATUS_PENDING
        assert data["build_plugin"] == "local-server"
        assert data["deploy_plugin"] == "local"
        assert data["test_plugin"] == "testpmd-memif"

    def test_request_has_metric_fields(self, tmp_path) -> None:
        path = create_request(
            seq=1,
            commit="abc123",
            campaign=SAMPLE_CAMPAIGN,
            description="Test",
            requests_dir=tmp_path,
        )
        data = json.loads(path.read_text())
        assert data["metric_name"] == "throughput_mpps"
        assert data["metric_path"] == "throughput_mpps"


class TestReadRequest:
    def test_reads_created_request(self, tmp_path) -> None:
        path = create_request(
            seq=1,
            commit="abc123",
            campaign=SAMPLE_CAMPAIGN,
            description="Test",
            requests_dir=tmp_path,
        )
        req = TestRequest.read(path)
        assert req.sequence == 1
        assert req.source_commit == "abc123"


class TestFindLatestRequest:
    def test_returns_none_when_empty(self, tmp_path) -> None:
        assert find_latest_request(requests_dir=tmp_path) is None

    def test_finds_highest_sequence(self, tmp_path) -> None:
        create_request(1, "aaa", SAMPLE_CAMPAIGN, "first", requests_dir=tmp_path)
        create_request(3, "ccc", SAMPLE_CAMPAIGN, "third", requests_dir=tmp_path)
        create_request(2, "bbb", SAMPLE_CAMPAIGN, "second", requests_dir=tmp_path)
        latest = find_latest_request(requests_dir=tmp_path)
        assert latest is not None
        assert latest.sequence == 3
        assert latest.source_commit == "ccc"
