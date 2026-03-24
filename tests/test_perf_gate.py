"""Tests for src.perf.gate."""

from __future__ import annotations

from src.perf.gate import EXIT_FAIL, EXIT_PASS, EXIT_WARN, check_regression


def _make_diff(changes: list[dict], assessment: str = "neutral") -> dict:
    return {"significant_changes": changes, "net_assessment": assessment}


class TestCheckRegression:
    def test_pass_no_changes(self):
        exit_code, report = check_regression(_make_diff([]))
        assert exit_code == EXIT_PASS

    def test_fail_large_regression(self):
        changes = [
            {"symbol": "hot_func", "delta_pct": 8.0, "verdict": "regressed"},
        ]
        exit_code, report = check_regression(
            _make_diff(changes),
            max_regression_pct=5.0,
        )
        assert exit_code == EXIT_FAIL
        assert any(c["result"] == "fail" for c in report["checks"])

    def test_warn_marginal_regression(self):
        changes = [
            {"symbol": "hot_func", "delta_pct": 3.5, "verdict": "regressed"},
        ]
        exit_code, report = check_regression(
            _make_diff(changes),
            max_regression_pct=5.0,
        )
        assert exit_code == EXIT_WARN

    def test_pass_below_warn_threshold(self):
        changes = [
            {"symbol": "hot_func", "delta_pct": 1.0, "verdict": "regressed"},
        ]
        exit_code, _ = check_regression(
            _make_diff(changes),
            max_regression_pct=5.0,
        )
        assert exit_code == EXIT_PASS

    def test_ipc_drop_fails(self):
        diff = _make_diff([])
        counter_diff = {
            "deltas": {
                "cycles": {"baseline": 100_000, "current": 100_000},
                "instructions": {"baseline": 80_000, "current": 70_000},
            },
        }
        exit_code, _ = check_regression(diff, counter_diff, max_ipc_drop=0.05)
        assert exit_code == EXIT_FAIL

    def test_throughput_overrides_warn(self):
        changes = [
            {"symbol": "hot_func", "delta_pct": 3.5, "verdict": "regressed"},
        ]
        exit_code, report = check_regression(
            _make_diff(changes),
            throughput_delta=1000.0,
        )
        assert exit_code == EXIT_PASS
        overrides = [c for c in report["checks"] if c["check"] == "throughput_override"]
        assert len(overrides) == 1

    def test_throughput_does_not_override_fail(self):
        changes = [
            {"symbol": "hot_func", "delta_pct": 8.0, "verdict": "regressed"},
        ]
        exit_code, _ = check_regression(
            _make_diff(changes),
            throughput_delta=1000.0,
            max_regression_pct=5.0,
        )
        assert exit_code == EXIT_FAIL

    def test_improvements_pass(self):
        changes = [
            {"symbol": "hot_func", "delta_pct": -5.0, "verdict": "improved"},
        ]
        exit_code, _ = check_regression(_make_diff(changes, "improved"))
        assert exit_code == EXIT_PASS
