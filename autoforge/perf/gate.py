"""CI regression gate for performance profiling."""

from __future__ import annotations

EXIT_PASS = 0
EXIT_FAIL = 1
EXIT_WARN = 2
EXIT_ERROR = 3


def check_regression(
    stack_diff: dict,
    counter_diff: dict | None = None,
    *,
    max_regression_pct: float = 5.0,
    max_ipc_drop: float = 0.05,
    throughput_delta: float | None = None,
) -> tuple[int, dict]:
    """Evaluate a profile diff against regression thresholds.

    Args:
        stack_diff: Output from diff_stacks().
        counter_diff: Output from diff_counters(), optional.
        max_regression_pct: Max allowed regression in any symbol (ppt).
        max_ipc_drop: Max allowed IPC decrease.
        throughput_delta: Throughput change (positive = improvement).

    Returns:
        Tuple of (exit_code, report_dict).
    """
    report: dict = {"checks": [], "exit_code": EXIT_PASS}
    worst_exit = EXIT_PASS

    # Check per-symbol regressions
    for change in stack_diff.get("significant_changes", []):
        if change["delta_pct"] > max_regression_pct:
            report["checks"].append(
                {
                    "check": "symbol_regression",
                    "symbol": change["symbol"],
                    "delta_pct": change["delta_pct"],
                    "threshold": max_regression_pct,
                    "result": "fail",
                }
            )
            worst_exit = max(worst_exit, EXIT_FAIL)
        elif change["delta_pct"] > max_regression_pct * 0.5:
            report["checks"].append(
                {
                    "check": "symbol_regression",
                    "symbol": change["symbol"],
                    "delta_pct": change["delta_pct"],
                    "threshold": max_regression_pct,
                    "result": "warn",
                }
            )
            worst_exit = max(worst_exit, EXIT_WARN)

    # Check IPC drop
    if counter_diff:
        ipc_delta = _extract_ipc_delta(counter_diff)
        if ipc_delta is not None and ipc_delta < -max_ipc_drop:
            report["checks"].append(
                {
                    "check": "ipc_drop",
                    "ipc_delta": ipc_delta,
                    "threshold": -max_ipc_drop,
                    "result": "fail",
                }
            )
            worst_exit = max(worst_exit, EXIT_FAIL)

    # Throughput improvement can override minor regressions
    if throughput_delta is not None and throughput_delta > 0 and worst_exit == EXIT_WARN:
        report["checks"].append(
            {
                "check": "throughput_override",
                "throughput_delta": throughput_delta,
                "result": "pass",
                "note": "Throughput improvement overrides minor profiling regressions",
            }
        )
        worst_exit = EXIT_PASS

    report["exit_code"] = worst_exit
    report["net_assessment"] = stack_diff.get("net_assessment", "unknown")
    return worst_exit, report


def _extract_ipc_delta(counter_diff: dict) -> float | None:
    """Extract IPC change from counter diff, if both cycles and instructions present."""
    deltas = counter_diff.get("deltas", {})

    base_cyc = base_inst = curr_cyc = curr_inst = None
    for key in ("cycles", "PM_RUN_CYC"):
        if key in deltas:
            base_cyc = deltas[key]["baseline"]
            curr_cyc = deltas[key]["current"]
            break
    for key in ("instructions", "PM_RUN_INST_CMPL"):
        if key in deltas:
            base_inst = deltas[key]["baseline"]
            curr_inst = deltas[key]["current"]
            break

    if not all(v and v > 0 for v in [base_cyc, base_inst, curr_cyc, curr_inst]):
        return None

    base_ipc = base_inst / base_cyc
    curr_ipc = curr_inst / curr_cyc
    return round(curr_ipc - base_ipc, 6)
