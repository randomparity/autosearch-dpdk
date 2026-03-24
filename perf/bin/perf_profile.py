#!/usr/bin/env python3
"""CLI entry point for perf profiling capture."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from src.perf.profile import profile_pid


def main() -> None:
    """Run perf profiling against a target PID."""
    parser = argparse.ArgumentParser(description="Capture perf profile of a running process")
    parser.add_argument("--pid", type=int, required=True, help="Target process ID")
    parser.add_argument("--duration", type=int, default=10, help="Capture duration in seconds")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("perf/results"),
        help="Directory for profiling artifacts",
    )
    parser.add_argument("--frequency", type=int, default=99, help="Sampling frequency (Hz)")
    parser.add_argument("--arch", default=None, help="Architecture override (auto-detected)")
    parser.add_argument("--sudo", action="store_true", help="Run perf commands with sudo")
    args = parser.parse_args()

    result = profile_pid(
        pid=args.pid,
        duration=args.duration,
        output_dir=args.output_dir,
        arch=args.arch,
        frequency=args.frequency,
        sudo=args.sudo,
    )

    summary = {
        "success": result.success,
        "duration_seconds": result.duration_seconds,
        "error": result.error,
        "total_stacks": len(result.folded_stacks),
        "counters": result.counters,
    }
    json.dump(summary, sys.stdout, indent=2)
    print()

    sys.exit(0 if result.success else 1)


if __name__ == "__main__":
    main()
