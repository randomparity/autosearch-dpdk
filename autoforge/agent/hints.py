"""Architecture-specific optimization hints lookup."""

from __future__ import annotations

from pathlib import Path
from typing import Any

KNOWN_ARCHES: frozenset[str] = frozenset(
    {
        "x86_64",
        "ppc64le",
        "aarch64",
        "s390x",
    }
)

TOPIC_SUFFIXES: dict[str, str] = {
    "optimization": "",
    "perf-counters": "-perf-counters",
}

DEFAULT_TOPIC = "optimization"

HINTS_DIR = Path(__file__).resolve().parent.parent.parent / "docs" / "arch-hints"


def hints_path(arch: str, topic: str = DEFAULT_TOPIC) -> Path:
    """Return the path to the arch hints file.

    Args:
        arch: Architecture identifier (e.g. "ppc64le").
        topic: Hint topic (e.g. "optimization", "perf-counters").

    Raises:
        ValueError: If arch or topic is not recognized.
        FileNotFoundError: If the hints file does not exist.
    """
    if arch not in KNOWN_ARCHES:
        msg = f"Unknown arch {arch!r}. Known: {', '.join(sorted(KNOWN_ARCHES))}"
        raise ValueError(msg)
    if topic not in TOPIC_SUFFIXES:
        msg = f"Unknown topic {topic!r}. Known: {', '.join(sorted(TOPIC_SUFFIXES))}"
        raise ValueError(msg)
    suffix = TOPIC_SUFFIXES[topic]
    path = HINTS_DIR / f"{arch}{suffix}.md"
    if not path.exists():
        msg = f"No {topic} hints for {arch!r} at {path}"
        raise FileNotFoundError(msg)
    return path


def hints_file_ref(arch: str, topic: str = DEFAULT_TOPIC) -> str:
    """Return a short summary pointing the agent to the hints file.

    Args:
        arch: Architecture identifier.
        topic: Hint topic.

    Returns:
        Multi-line string with the file path and reading instructions.
    """
    path = hints_path(arch, topic)
    with path.open() as fh:
        line_count = sum(1 for _ in fh)
    return (
        f"Architecture {topic} hints for {arch}: {path}\n"
        f"({line_count} lines — read this file for"
        f" {topic} guidance)"
    )


def list_topics(arch: str) -> list[str]:
    """Return available hint topics for an architecture.

    Args:
        arch: Architecture identifier.

    Raises:
        ValueError: If arch is not recognized.
    """
    if arch not in KNOWN_ARCHES:
        msg = f"Unknown arch {arch!r}. Known: {', '.join(sorted(KNOWN_ARCHES))}"
        raise ValueError(msg)
    topics = []
    for topic_name, suffix in TOPIC_SUFFIXES.items():
        path = HINTS_DIR / f"{arch}{suffix}.md"
        if path.exists():
            topics.append(topic_name)
    return sorted(topics)


_CACHE_LINE_SIZES: dict[str, int] = {
    "x86_64": 64,
    "ppc64le": 128,
    "aarch64": 64,
    "s390x": 256,
}


def workload_hints(arch: str, profile_summary: dict[str, Any]) -> str:
    """Generate workload-specific optimization suggestions from profiling data.

    Args:
        arch: Architecture identifier (e.g. "ppc64le").
        profile_summary: Profile summary dict with top_functions,
            derived_metrics, and diagnostics keys.

    Returns:
        Multi-line markdown string with data-driven suggestions,
        or empty string if no suggestions apply.
    """
    suggestions: list[str] = []
    derived = profile_summary.get("derived_metrics") or {}
    top_fns = profile_summary.get("top_functions") or []
    cache_line = _CACHE_LINE_SIZES.get(arch, 64)

    # Check backend-bound ratio
    backend = derived.get("backend_bound")
    if backend is not None and backend > 0.3:
        suggestions.append(
            f"Backend-bound is high ({backend:.1%}). Focus on memory access patterns,"
            f" cache alignment (cache line = {cache_line}B), and data locality."
        )

    # Check L1D miss rate
    l1d = derived.get("l1d_miss_rate")
    if l1d is not None and l1d > 0.05:
        suggestions.append(
            f"L1D cache miss rate is elevated ({l1d:.2%}). Consider interleaving"
            " per-element processing, reducing working set, or aligning structures"
            f" to {cache_line}-byte boundaries."
        )

    # Check IPC
    ipc = derived.get("ipc")
    if ipc is not None and ipc < 1.0:
        suggestions.append(
            f"IPC is low ({ipc:.2f}). Pipeline stalls likely from memory latency"
            " or branch mispredictions. Check for data-dependent branches in hot loops."
        )

    # Check hot functions for common patterns
    fn_names = [f.get("name", "") for f in top_fns[:10]]
    fn_pcts = {f.get("name", ""): f.get("pct", 0) for f in top_fns[:10]}

    memcpy_fns = [n for n in fn_names if "memcpy" in n.lower() or "rte_mov" in n.lower()]
    if memcpy_fns:
        total_pct = sum(fn_pcts.get(n, 0) for n in memcpy_fns)
        suggestions.append(
            f"Memory copy functions ({', '.join(memcpy_fns)}) account for"
            f" {total_pct:.1f}% of samples. Optimize copy paths: separate loads"
            " from stores, use architecture-native vector instructions, consider"
            " batch sizes that align with cache lines."
        )

    alloc_fns = [n for n in fn_names if "alloc" in n.lower() or "mempool" in n.lower()]
    if alloc_fns:
        total_pct = sum(fn_pcts.get(n, 0) for n in alloc_fns)
        suggestions.append(
            f"Allocation functions ({', '.join(alloc_fns)}) account for"
            f" {total_pct:.1f}% of samples. Consider bulk allocation, forward"
            " copies instead of per-element loops, and memory channel spreading."
        )

    if not suggestions:
        return ""

    lines = ["Workload-specific suggestions (from profiling data):"]
    for i, s in enumerate(suggestions, 1):
        lines.append(f"  {i}. {s}")
    return "\n".join(lines)
