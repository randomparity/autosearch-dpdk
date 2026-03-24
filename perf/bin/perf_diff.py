#!/usr/bin/env python3
"""CLI entry point for differential profile comparison."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from src.perf.diff import diff_stacks, load_folded


def main() -> None:
    """Compare two folded-stack profiles."""
    parser = argparse.ArgumentParser(description="Diff two perf profiles")
    parser.add_argument("--baseline", type=Path, required=True, help="Baseline folded-stack file")
    parser.add_argument("--current", type=Path, required=True, help="Current folded-stack file")
    parser.add_argument("--output", type=Path, help="Output JSON file (default: stdout)")
    parser.add_argument("--threshold", type=float, default=1.0, help="Min delta pct to report")
    args = parser.parse_args()

    try:
        baseline = load_folded(args.baseline)
        current = load_folded(args.current)
    except FileNotFoundError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    result = diff_stacks(baseline, current, threshold=args.threshold)

    if args.output:
        with open(args.output, "w") as f:
            json.dump(result, f, indent=2)
    else:
        json.dump(result, sys.stdout, indent=2)
        print()


if __name__ == "__main__":
    main()
