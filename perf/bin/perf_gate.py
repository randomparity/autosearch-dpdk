#!/usr/bin/env python3
"""CLI entry point for CI regression gate."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from src.perf.gate import check_regression


def main() -> None:
    """Evaluate a profile diff against regression thresholds."""
    parser = argparse.ArgumentParser(description="Performance regression gate")
    parser.add_argument("--diff", type=Path, required=True, help="Stack diff JSON file")
    parser.add_argument("--counters-diff", type=Path, help="Counter diff JSON file")
    parser.add_argument(
        "--max-regression",
        type=float,
        default=5.0,
        help="Max regression ppt",
    )
    parser.add_argument("--max-ipc-drop", type=float, default=0.05)
    parser.add_argument("--throughput-delta", type=float)
    parser.add_argument("--output", type=Path, help="Output report JSON (default: stdout)")
    args = parser.parse_args()

    try:
        with open(args.diff) as f:
            stack_diff = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        print(f"Error loading {args.diff}: {exc}", file=sys.stderr)
        sys.exit(1)

    counter_diff = None
    if args.counters_diff:
        try:
            with open(args.counters_diff) as f:
                counter_diff = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError) as exc:
            print(f"Error loading {args.counters_diff}: {exc}", file=sys.stderr)
            sys.exit(1)

    exit_code, report = check_regression(
        stack_diff,
        counter_diff,
        max_regression_pct=args.max_regression,
        max_ipc_drop=args.max_ipc_drop,
        throughput_delta=args.throughput_delta,
    )

    if args.output:
        with open(args.output, "w") as f:
            json.dump(report, f, indent=2)
    else:
        json.dump(report, sys.stdout, indent=2)
        print()

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
