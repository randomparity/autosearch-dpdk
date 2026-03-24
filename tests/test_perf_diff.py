"""Tests for src.perf.diff."""

from __future__ import annotations

from pathlib import Path

from src.perf.diff import diff_counters, diff_stacks, load_folded

BASELINE_STACKS = {
    "main;func_a;hot_func": 50,
    "main;func_b;other_func": 30,
    "main;func_c": 20,
}

IMPROVED_STACKS = {
    "main;func_a;hot_func": 20,  # hot_func got cooler: 50% → 20%
    "main;func_b;other_func": 35,  # slight shift: 30% → 35%
    "main;func_c": 25,  # slight shift: 20% → 25%
    "main;func_d;new_func": 20,  # new function at 20%, less than hot_func saved
}

REGRESSED_STACKS = {
    "main;func_a;hot_func": 70,  # got hotter: 50% → 70%
    "main;func_b;other_func": 20,  # cooled: 30% → 20%
    "main;func_c": 10,  # cooled: 20% → 10%
}


class TestDiffStacks:
    def test_improved(self):
        result = diff_stacks(BASELINE_STACKS, IMPROVED_STACKS, threshold=1.0)
        assert result["net_assessment"] == "improved"
        symbols = {c["symbol"] for c in result["significant_changes"]}
        assert "hot_func" in symbols

    def test_regressed(self):
        result = diff_stacks(BASELINE_STACKS, REGRESSED_STACKS, threshold=1.0)
        assert result["net_assessment"] == "regressed"
        hot_change = next(c for c in result["significant_changes"] if c["symbol"] == "hot_func")
        assert hot_change["verdict"] == "regressed"
        assert hot_change["delta_pct"] > 0

    def test_identical(self):
        result = diff_stacks(BASELINE_STACKS, BASELINE_STACKS, threshold=1.0)
        assert result["significant_changes"] == []
        assert result["net_assessment"] == "neutral"

    def test_threshold_filters(self):
        result = diff_stacks(BASELINE_STACKS, IMPROVED_STACKS, threshold=50.0)
        # High threshold filters out all changes
        assert result["significant_changes"] == []

    def test_sample_counts(self):
        result = diff_stacks(BASELINE_STACKS, IMPROVED_STACKS)
        assert result["baseline_total_samples"] == 100
        assert result["current_total_samples"] == 100

    def test_empty_baseline(self):
        result = diff_stacks({}, BASELINE_STACKS, threshold=1.0)
        assert len(result["significant_changes"]) > 0

    def test_empty_both(self):
        result = diff_stacks({}, {})
        assert result["significant_changes"] == []


class TestDiffCounters:
    def test_basic(self):
        baseline = {"cycles": 100_000, "instructions": 80_000}
        current = {"cycles": 90_000, "instructions": 85_000}
        result = diff_counters(baseline, current)
        assert "cycles" in result["deltas"]
        assert result["deltas"]["cycles"]["change_pct"] == -10.0
        assert result["deltas"]["instructions"]["change_pct"] == 6.25

    def test_new_event(self):
        result = diff_counters({"cycles": 100}, {"cycles": 100, "new_event": 50})
        assert "new_event" in result["deltas"]

    def test_empty(self):
        result = diff_counters({}, {})
        assert result["deltas"] == {}


class TestLoadFolded:
    def test_roundtrip(self, tmp_path: Path):
        content = "a;b;c 10\nd;e 5\nf 1\n"
        path = tmp_path / "test.folded"
        path.write_text(content)

        stacks = load_folded(path)
        assert stacks == {"a;b;c": 10, "d;e": 5, "f": 1}

    def test_empty_file(self, tmp_path: Path):
        path = tmp_path / "empty.folded"
        path.write_text("")
        assert load_folded(path) == {}

    def test_malformed_lines_skipped(self, tmp_path: Path):
        content = "a;b;c 10\nbadline\nd;e 5\n"
        path = tmp_path / "mixed.folded"
        path.write_text(content)
        stacks = load_folded(path)
        assert len(stacks) == 2
