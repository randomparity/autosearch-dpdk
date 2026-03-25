"""Tests for src.perf.analyze."""

from __future__ import annotations

from autoforge.perf.analyze import (
    compute_derived_metrics,
    diagnose,
    hot_paths,
    summarize,
    top_functions,
)

SAMPLE_STACKS = {
    "main;mlx5_rx_burst;rte_eth_rx_burst": 50,
    "main;mlx5_rx_burst;rte_memcpy": 30,
    "main;mlx5_tx_burst;rte_eth_tx_burst": 15,
    "main;rte_ring_dequeue": 5,
}

SAMPLE_COUNTERS_X86 = {
    "cycles": 89_400_000_000,
    "instructions": 71_200_000_000,
    "L1-dcache-load-misses": 450_000_000,
    "LLC-load-misses": 10_000_000,
    "branch-misses": 120_000_000,
    "stalled-cycles-frontend": 5_000_000_000,
    "stalled-cycles-backend": 35_000_000_000,
}

X86_PROFILE = {
    "arch": "x86_64",
    "events": {
        "cycles": "cycles",
        "instructions": "instructions",
        "l1d_miss": "L1-dcache-load-misses",
        "llc_miss": "LLC-load-misses",
        "branch_miss": "branch-misses",
        "stalled_frontend": "stalled-cycles-frontend",
        "stalled_backend": "stalled-cycles-backend",
    },
    "derived_metrics": {
        "ipc": "instructions / cycles",
        "l1d_miss_rate": "l1d_miss / instructions",
        "backend_bound": "stalled_backend / cycles",
    },
    "heuristics": [
        {
            "condition": "ipc < 1.0",
            "diagnosis": "Pipeline underutilized.",
            "suggestions": ["Check cache misses."],
        },
        {
            "condition": "backend_bound > 0.4",
            "diagnosis": "Backend stall.",
            "suggestions": ["Check memory access patterns."],
        },
    ],
}


class TestTopFunctions:
    def test_basic_ordering(self):
        result = top_functions(SAMPLE_STACKS)
        assert len(result) == 4
        assert result[0]["name"] == "rte_eth_rx_burst"
        assert result[0]["samples"] == 50
        assert result[0]["pct"] == 50.0

    def test_limit(self):
        result = top_functions(SAMPLE_STACKS, limit=2)
        assert len(result) == 2

    def test_empty(self):
        assert top_functions({}) == []

    def test_percentages_sum(self):
        result = top_functions(SAMPLE_STACKS)
        total_pct = sum(r["pct"] for r in result)
        assert abs(total_pct - 100.0) < 0.1


class TestHotPaths:
    def test_basic(self):
        result = hot_paths(SAMPLE_STACKS, depth=3, limit=5)
        assert len(result) > 0
        # Hottest path should be the rx_burst one
        assert result[0]["samples"] == 50

    def test_depth_truncation(self):
        deep = {"a;b;c;d;e;f": 10}
        result = hot_paths(deep, depth=3)
        assert result[0]["path"] == "d;e;f"

    def test_empty(self):
        assert hot_paths({}) == []


class TestComputeDerivedMetrics:
    def test_ipc(self):
        derived = compute_derived_metrics(SAMPLE_COUNTERS_X86, X86_PROFILE)
        expected_ipc = 71_200_000_000 / 89_400_000_000
        assert abs(derived["ipc"] - expected_ipc) < 0.001

    def test_backend_bound(self):
        derived = compute_derived_metrics(SAMPLE_COUNTERS_X86, X86_PROFILE)
        expected = 35_000_000_000 / 89_400_000_000
        assert abs(derived["backend_bound"] - expected) < 0.001

    def test_missing_counters(self):
        derived = compute_derived_metrics({}, X86_PROFILE)
        assert derived == {}


class TestDiagnose:
    def test_triggers_ipc_heuristic(self):
        # IPC = 0.797, which is < 1.0
        result = diagnose(SAMPLE_COUNTERS_X86, SAMPLE_STACKS, X86_PROFILE)
        categories = [d["category"] for d in result]
        assert "pipeline_utilization" in categories

    def test_no_triggers_with_good_metrics(self):
        good_counters = {
            "cycles": 50_000_000_000,
            "instructions": 100_000_000_000,  # IPC = 2.0
            "stalled-cycles-backend": 5_000_000_000,  # 10% backend
        }
        result = diagnose(good_counters, SAMPLE_STACKS, X86_PROFILE)
        # IPC > 1.0, backend < 0.4, so no triggers
        assert len(result) == 0

    def test_empty_stacks(self):
        result = diagnose(SAMPLE_COUNTERS_X86, {}, X86_PROFILE)
        # Should still evaluate counter-based heuristics
        assert isinstance(result, list)


class TestSummarize:
    def test_keys(self):
        result = summarize(SAMPLE_COUNTERS_X86, SAMPLE_STACKS, X86_PROFILE)
        assert "top_functions" in result
        assert "derived_metrics" in result
        assert "diagnostics" in result
        assert "total_samples" in result
        assert result["total_samples"] == 100

    def test_empty(self):
        result = summarize({}, {}, X86_PROFILE)
        assert result["total_samples"] == 0
        assert result["top_functions"] == []
