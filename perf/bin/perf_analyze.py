#!/usr/bin/env python3
"""CLI entry point for perf analysis subcommands."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from src.perf.analyze import (
    diagnose,
    hot_paths,
    summarize,
    top_functions,
)
from src.perf.arch import load_arch_profile
from src.perf.diff import load_folded


def cmd_top_functions(args: argparse.Namespace) -> None:
    """Print top functions by sample count."""
    stacks = load_folded(args.folded)
    result = top_functions(stacks, limit=args.limit)
    json.dump(result, sys.stdout, indent=2)
    print()


def cmd_hot_paths(args: argparse.Namespace) -> None:
    """Print top hot paths."""
    stacks = load_folded(args.folded)
    result = hot_paths(stacks, depth=args.depth, limit=args.limit)
    json.dump(result, sys.stdout, indent=2)
    print()


def cmd_diagnose(args: argparse.Namespace) -> None:
    """Print diagnostic analysis."""
    stacks = load_folded(args.folded)
    counters = _load_json(args.counters) if args.counters else {}
    profile = load_arch_profile(args.arch)
    result = diagnose(counters, stacks, profile)
    json.dump(result, sys.stdout, indent=2)
    print()


def cmd_summarize(args: argparse.Namespace) -> None:
    """Print compact summary."""
    stacks = load_folded(args.folded)
    counters = _load_json(args.counters) if args.counters else {}
    profile = load_arch_profile(args.arch)
    result = summarize(counters, stacks, profile)
    json.dump(result, sys.stdout, indent=2)
    print()


def _load_json(path: Path) -> dict:
    """Load a JSON file, exiting with an error message on failure."""
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        print(f"Error loading {path}: {exc}", file=sys.stderr)
        sys.exit(1)


def main() -> None:
    """Parse arguments and dispatch subcommand."""
    parser = argparse.ArgumentParser(description="Analyze perf profiling data")
    sub = parser.add_subparsers(dest="command", required=True)

    top = sub.add_parser("top-functions", help="Top functions by CPU samples")
    top.add_argument("--folded", type=Path, required=True, help="Folded-stack file")
    top.add_argument("--limit", type=int, default=20)
    top.set_defaults(func=cmd_top_functions)

    hp = sub.add_parser("hot-paths", help="Top hot call paths")
    hp.add_argument("--folded", type=Path, required=True)
    hp.add_argument("--depth", type=int, default=5)
    hp.add_argument("--limit", type=int, default=10)
    hp.set_defaults(func=cmd_hot_paths)

    diag = sub.add_parser("diagnose", help="Heuristic-driven diagnosis")
    diag.add_argument("--folded", type=Path, required=True)
    diag.add_argument("--counters", type=Path, help="Counters JSON file")
    diag.add_argument("--arch", default=None)
    diag.set_defaults(func=cmd_diagnose)

    summ = sub.add_parser("summarize", help="Compact summary for agent")
    summ.add_argument("--folded", type=Path, required=True)
    summ.add_argument("--counters", type=Path, help="Counters JSON file")
    summ.add_argument("--arch", default=None)
    summ.set_defaults(func=cmd_summarize)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
