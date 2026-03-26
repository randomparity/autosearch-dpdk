"""Tests for history TSV management."""

from __future__ import annotations

from pathlib import Path

from autoforge.agent.history import append_result, best_result, load_history


def make_tsv(tmp_path: Path) -> Path:
    """Create a results.tsv with header."""
    path = tmp_path / "results.tsv"
    path.write_text("sequence\ttimestamp\tsource_commit\tmetric_value\tstatus\tdescription\n")
    return path


class TestAppendResult:
    def test_appends_row(self, tmp_path) -> None:
        path = make_tsv(tmp_path)
        append_result(1, "abc123", 14.5, "completed", "First attempt", path=path)
        rows = load_history(path=path)
        assert len(rows) == 1
        assert rows[0]["sequence"] == "1"
        assert rows[0]["source_commit"] == "abc123"
        assert rows[0]["metric_value"] == "14.5"

    def test_appends_multiple_rows(self, tmp_path) -> None:
        path = make_tsv(tmp_path)
        append_result(1, "abc123", 14.5, "completed", "First", path=path)
        append_result(2, "def456", 15.0, "completed", "Second", path=path)
        rows = load_history(path=path)
        assert len(rows) == 2

    def test_appends_none_metric(self, tmp_path) -> None:
        path = make_tsv(tmp_path)
        append_result(1, "abc123", None, "failed", "Build broke", path=path)
        rows = load_history(path=path)
        assert len(rows) == 1
        assert rows[0]["metric_value"] == ""

    def test_timestamp_is_iso_format(self, tmp_path) -> None:
        path = make_tsv(tmp_path)
        append_result(1, "abc123", 14.5, "completed", "Test", path=path)
        rows = load_history(path=path)
        ts = rows[0]["timestamp"]
        assert "T" in ts


class TestAppendResultIdempotency:
    def test_duplicate_sequence_skipped(self, tmp_path) -> None:
        path = make_tsv(tmp_path)
        append_result(1, "abc123", 14.5, "completed", "First attempt", path=path)
        append_result(1, "abc123", 14.5, "completed", "First attempt", path=path)
        rows = load_history(path=path)
        assert len(rows) == 1

    def test_different_sequences_allowed(self, tmp_path) -> None:
        path = make_tsv(tmp_path)
        append_result(1, "abc123", 14.5, "completed", "First", path=path)
        append_result(2, "def456", 15.0, "completed", "Second", path=path)
        rows = load_history(path=path)
        assert len(rows) == 2


class TestLoadHistory:
    def test_empty_file_returns_empty_list(self, tmp_path) -> None:
        path = make_tsv(tmp_path)
        assert load_history(path=path) == []

    def test_loads_rows_as_dicts(self, tmp_path) -> None:
        path = make_tsv(tmp_path)
        append_result(1, "abc", 10.0, "completed", "test", path=path)
        rows = load_history(path=path)
        assert isinstance(rows[0], dict)
        assert "sequence" in rows[0]
        assert "timestamp" in rows[0]


class TestAppendResultWithTags:
    def test_tags_written_as_csv(self, tmp_path) -> None:
        path = tmp_path / "results.tsv"
        path.write_text(
            "sequence\ttimestamp\tsource_commit\tmetric_value\tstatus\tdescription\ttags\n"
        )
        append_result(
            1,
            "abc123",
            14.5,
            "completed",
            "Test",
            path=path,
            tags=["memcpy", "cache"],
        )
        rows = load_history(path=path)
        assert len(rows) == 1
        assert rows[0]["tags"] == "memcpy,cache"

    def test_no_tags_writes_empty(self, tmp_path) -> None:
        path = tmp_path / "results.tsv"
        path.write_text(
            "sequence\ttimestamp\tsource_commit\tmetric_value\tstatus\tdescription\ttags\n"
        )
        append_result(1, "abc123", 14.5, "completed", "Test", path=path)
        rows = load_history(path=path)
        assert rows[0]["tags"] == ""


class TestBestResult:
    def test_maximize(self, tmp_path) -> None:
        path = make_tsv(tmp_path)
        append_result(1, "a", 10.0, "completed", "low", path=path)
        append_result(2, "b", 15.0, "completed", "high", path=path)
        append_result(3, "c", 12.0, "completed", "mid", path=path)
        best = best_result(path=path, direction="maximize")
        assert best is not None
        assert best["metric_value"] == "15.0"

    def test_minimize(self, tmp_path) -> None:
        path = make_tsv(tmp_path)
        append_result(1, "a", 10.0, "completed", "low", path=path)
        append_result(2, "b", 15.0, "completed", "high", path=path)
        best = best_result(path=path, direction="minimize")
        assert best is not None
        assert best["metric_value"] == "10.0"

    def test_skips_empty_metrics(self, tmp_path) -> None:
        path = make_tsv(tmp_path)
        append_result(1, "a", None, "failed", "broke", path=path)
        append_result(2, "b", 14.0, "completed", "ok", path=path)
        best = best_result(path=path, direction="maximize")
        assert best is not None
        assert best["source_commit"] == "b"

    def test_empty_history_returns_none(self, tmp_path) -> None:
        path = make_tsv(tmp_path)
        assert best_result(path=path) is None

    def test_all_failed_returns_none(self, tmp_path) -> None:
        path = make_tsv(tmp_path)
        append_result(1, "a", None, "failed", "broke", path=path)
        append_result(2, "b", None, "failed", "broke again", path=path)
        assert best_result(path=path) is None
