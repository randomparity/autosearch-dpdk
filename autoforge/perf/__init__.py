"""Performance profiling library for DPDK optimization."""

from __future__ import annotations

from autoforge.perf.analyze import (
    compute_derived_metrics,
    diagnose,
    hot_paths,
    summarize,
    top_functions,
)
from autoforge.perf.arch import COMMON_EVENTS, detect_arch, load_arch_profile
from autoforge.perf.diff import diff_counters, diff_stacks, load_folded
from autoforge.perf.gate import EXIT_ERROR, EXIT_FAIL, EXIT_PASS, EXIT_WARN, check_regression
from autoforge.perf.profile import (
    ProfileResult,
    fold_stacks,
    parse_perf_stat,
    profile_pid,
    write_folded,
)

__all__ = [
    "COMMON_EVENTS",
    "EXIT_ERROR",
    "EXIT_FAIL",
    "EXIT_PASS",
    "EXIT_WARN",
    "ProfileResult",
    "check_regression",
    "compute_derived_metrics",
    "detect_arch",
    "diagnose",
    "diff_counters",
    "diff_stacks",
    "fold_stacks",
    "hot_paths",
    "load_arch_profile",
    "load_folded",
    "parse_perf_stat",
    "profile_pid",
    "summarize",
    "top_functions",
    "write_folded",
]
