"""Tests for sprint summary generation."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from autoforge.agent.summarize import (
    DEFAULT_TEMPLATE,
    _build_accepted_table,
    _build_rejected_table,
    _build_tags_summary,
    _first_completed,
    _load_template,
    _render,
    _SafeDict,
    _scored_rows,
    generate_summary,
)


def _row(seq: str, metric: str, status: str, desc: str, tags: str = "") -> dict:
    return {
        "sequence": seq,
        "metric_value": metric,
        "status": status,
        "description": desc,
        "tags": tags,
    }


SAMPLE_CAMPAIGN: dict = {
    "campaign": {"name": "test-opt", "max_iterations": 50},
    "metric": {"name": "throughput_mpps", "path": "throughput_mpps", "direction": "maximize"},
    "project": {"name": "dpdk", "scope": ["drivers/net/memif/"]},
    "goal": {"description": "Optimize memif throughput"},
    "platform": {"arch": "ppc64le"},
}


class TestScoredRows:
    def test_sorts_by_best_maximize(self) -> None:
        history = [_row("1", "10.0", "completed", "a"), _row("2", "15.0", "completed", "b")]
        result = _scored_rows(history, "maximize")
        assert result[0]["value"] == 15.0

    def test_sorts_by_best_minimize(self) -> None:
        history = [_row("1", "10.0", "completed", "a"), _row("2", "15.0", "completed", "b")]
        result = _scored_rows(history, "minimize")
        assert result[0]["value"] == 10.0

    def test_skips_empty_metrics(self) -> None:
        history = [_row("1", "", "failed", "broke"), _row("2", "15.0", "completed", "ok")]
        result = _scored_rows(history, "maximize")
        assert len(result) == 1


class TestFirstCompleted:
    def test_finds_first(self) -> None:
        history = [
            _row("1", "", "failed", "broke"),
            _row("2", "10.0", "completed", "baseline"),
            _row("3", "12.0", "completed", "better"),
        ]
        result = _first_completed(history)
        assert result is not None
        assert result["value"] == 10.0
        assert result["sequence"] == "2"

    def test_empty_returns_none(self) -> None:
        assert _first_completed([]) is None

    def test_all_failed_returns_none(self) -> None:
        history = [_row("1", "", "failed", "broke")]
        assert _first_completed(history) is None


class TestBuildAcceptedTable:
    def test_with_improvements(self) -> None:
        history = [
            _row("1", "10.0", "completed", "baseline"),
            _row("2", "12.0", "completed", "improvement"),
            _row("3", "11.0", "completed", "regression"),
            _row("4", "14.0", "completed", "big improvement"),
        ]
        baseline = {"sequence": "1", "value": 10.0}
        table = _build_accepted_table(history, baseline, "maximize")
        assert "| 1 |" in table
        assert "| 2 |" in table
        assert "improvement" in table
        assert "big improvement" in table
        # Regression should not appear as accepted
        assert "regression" not in table

    def test_no_baseline(self) -> None:
        result = _build_accepted_table([], None, "maximize")
        assert "No accepted patches" in result


class TestBuildRejectedTable:
    def test_with_failures(self) -> None:
        failures = [
            {"metric_value": "8.0", "description": "bad idea", "diff_summary": "1 file"},
        ]
        table = _build_rejected_table(failures)
        assert "bad idea" in table
        assert "8.0" in table

    def test_empty(self) -> None:
        assert "No rejected" in _build_rejected_table([])


class TestBuildTagsSummary:
    def test_with_tags(self) -> None:
        history = [
            _row("1", "10", "completed", "a", "memcpy,cache"),
            _row("2", "12", "completed", "b", "memcpy"),
        ]
        result = _build_tags_summary(history)
        assert "memcpy: 2" in result
        assert "cache: 1" in result

    def test_no_tags(self) -> None:
        history = [_row("1", "10", "completed", "a")]
        assert _build_tags_summary(history) == ""


class TestRender:
    def test_basic_substitution(self) -> None:
        template = "Hello {name}, you have {count} items."
        data = {"name": "World", "count": "3"}
        assert _render(template, data) == "Hello World, you have 3 items."

    def test_missing_key_falls_back(self) -> None:
        template = "Hello {name}, status: {missing_key}."
        data = {"name": "World"}
        result = _render(template, data)
        assert "World" in result
        assert "missing_key" in result


class TestSafeDict:
    def test_returns_placeholder(self) -> None:
        d = _SafeDict({"a": "1"})
        assert d["b"] == "{{ b }}"


class TestLoadTemplate:
    def test_default_template(self) -> None:
        template = _load_template({"project": {"name": "nonexistent"}})
        assert template == DEFAULT_TEMPLATE

    def test_custom_template(self, tmp_path: Path) -> None:
        custom = "Custom: {sprint_name}"
        template_path = tmp_path / "projects" / "test" / "summary-template.md"
        template_path.parent.mkdir(parents=True)
        template_path.write_text(custom)
        with patch("autoforge.agent.summarize.REPO_ROOT", tmp_path):
            result = _load_template({"project": {"name": "test"}})
        assert result == custom


class TestGenerateSummary:
    def test_end_to_end(self, tmp_path: Path) -> None:
        """Full generation with mock sprint data."""
        # Set up sprint directory
        sprint = tmp_path / "projects" / "dpdk" / "sprints" / "2026-01-01-test"
        (sprint / "requests").mkdir(parents=True)
        (sprint / "docs").mkdir()

        # Write results
        results = sprint / "results.tsv"
        results.write_text(
            "sequence\ttimestamp\tsource_commit\tmetric_value\tstatus\tdescription\ttags\n"
            "1\t2026-01-01T00:00:00\tabc\t10.0\tcompleted\tbaseline\t\n"
            "2\t2026-01-01T01:00:00\tdef\t12.0\tcompleted\timprovement\tmemcpy\n"
        )

        pointer = {"project": "dpdk", "sprint": "2026-01-01-test"}
        with (
            patch("autoforge.agent.sprint.REPO_ROOT", tmp_path),
            patch("autoforge.agent.sprint.load_pointer", return_value=pointer),
            patch("autoforge.agent.summarize.REPO_ROOT", tmp_path),
        ):
            text = generate_summary(SAMPLE_CAMPAIGN)

        assert "2026-01-01-test" in text
        assert "10.00" in text
        assert "12.00" in text
        assert "improvement" in text
