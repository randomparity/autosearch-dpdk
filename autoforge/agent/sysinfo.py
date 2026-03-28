"""System information collection for sprint summaries."""

from __future__ import annotations

import json
import logging
import os
import platform
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

VALID_ROLES = ("agent", "build", "test", "runner")


def collect_sysinfo() -> dict[str, Any]:
    """Collect system information from the current machine.

    All fields are best-effort — failures are logged and result in
    empty/default values, never exceptions.
    """
    info: dict[str, Any] = {
        "hostname": platform.node(),
        "os": f"{platform.system()} {platform.release()}",
        "kernel": _kernel_version(),
        "arch": platform.machine(),
        "cpu_model": _cpu_model(),
        "cpu_count_physical": _physical_cpu_count(),
        "cpu_count_logical": os.cpu_count(),
        "memory_gb": _memory_gb(),
        "python_version": platform.python_version(),
        "gpu": _gpu_info(),
        "compiler": _compiler_version(),
    }
    return info


def save_sysinfo(role: str, output_dir: Path) -> Path:
    """Collect sysinfo, tag with role and timestamp, write to JSON.

    Args:
        role: Machine role (agent, build, test, runner).
        output_dir: Directory to write the JSON file into.

    Returns:
        Path to the written JSON file.
    """
    if role not in VALID_ROLES:
        msg = f"Invalid role {role!r}, must be one of {VALID_ROLES}"
        raise ValueError(msg)

    info = collect_sysinfo()
    info["role"] = role
    info["collected_at"] = datetime.now(UTC).isoformat()

    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"sysinfo-{role}.json"
    path.write_text(json.dumps(info, indent=2) + "\n")
    return path


def load_all_sysinfo(
    docs_dir: Path,
    requests_dir: Path | None = None,
) -> dict[str, dict[str, Any]]:
    """Load sysinfo from JSON files and completed request results.

    Checks two sources:
    1. ``sysinfo-*.json`` files in *docs_dir* (agent-side collection).
    2. ``runner_sysinfo`` embedded in completed request results in
       *requests_dir* (runner-side collection, preferred for runner role).

    Runner-side sysinfo from requests takes precedence over stale
    ``sysinfo-runner.json`` files since it's collected on the actual
    runner machine.

    Returns:
        Mapping of role to info dict.
    """
    result: dict[str, dict[str, Any]] = {}
    for path in sorted(docs_dir.glob("sysinfo-*.json")):
        try:
            data = json.loads(path.read_text())
            role = data.get("role", path.stem.removeprefix("sysinfo-"))
            result[role] = data
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to load %s: %s", path, exc)

    # Extract runner sysinfo from the most recent completed request.
    if requests_dir is not None and requests_dir.is_dir():
        runner_info = _extract_runner_sysinfo_from_requests(requests_dir)
        if runner_info:
            result["runner"] = runner_info

    return result


def _extract_runner_sysinfo_from_requests(
    requests_dir: Path,
) -> dict[str, Any] | None:
    """Scan completed requests for embedded runner_sysinfo.

    Returns the sysinfo from the most recent completed request that
    contains it, or None.
    """
    from autoforge.protocol import TestRequest

    for path in sorted(requests_dir.glob("*.json"), reverse=True):
        try:
            req = TestRequest.read(path)
        except (ValueError, KeyError, TypeError, OSError):
            continue
        if req.status != "completed" or not req.results_json:
            continue
        sysinfo = req.results_json.get("runner_sysinfo")
        if sysinfo and isinstance(sysinfo, dict):
            return sysinfo
    return None


def render_sysinfo_section(
    all_info: dict[str, dict[str, Any]],
) -> str:
    """Render system info into a markdown section for the summary."""
    if not all_info:
        return "<!-- Run 'autoforge sysinfo --role runner' on each machine -->"

    hostnames = {info.get("hostname") for info in all_info.values()}
    same_host = len(hostnames) == 1

    header = "## System Info\n\n"
    if same_host:
        header += "_All phases run on the same host._\n\n"

    fields = [
        ("hostname", "Hostname"),
        ("os", "OS"),
        ("kernel", "Kernel"),
        ("arch", "Architecture"),
        ("cpu_model", "CPU"),
        ("cpu_count_physical", "Physical cores"),
        ("cpu_count_logical", "Logical cores"),
        ("memory_gb", "Memory (GB)"),
        ("gpu", "GPU"),
        ("compiler", "Compiler"),
        ("python_version", "Python"),
    ]

    roles = sorted(all_info.keys())
    col_headers = ["Property"] + [r.capitalize() for r in roles]
    lines = [
        "| " + " | ".join(col_headers) + " |",
        "| " + " | ".join("---" for _ in col_headers) + " |",
    ]

    for key, label in fields:
        row = [label]
        for role in roles:
            val = all_info[role].get(key, "")
            if isinstance(val, list):
                val = ", ".join(str(v) for v in val) or "N/A"
            elif val is None:
                val = "N/A"
            else:
                val = str(val)
            row.append(val)
        lines.append("| " + " | ".join(row) + " |")

    return header + "\n".join(lines)


# --- Private helpers ---


def _kernel_version() -> str:
    system = platform.system()
    if system == "Linux":
        return platform.release()
    if system == "Darwin":
        return platform.version()
    return platform.release()


def _cpu_model() -> str:
    system = platform.system()
    if system == "Linux":
        return _cpu_model_linux()
    if system == "Darwin":
        return _cpu_model_darwin()
    return platform.processor() or "unknown"


def _cpu_model_linux() -> str:
    try:
        text = Path("/proc/cpuinfo").read_text()
        for line in text.splitlines():
            if line.startswith("model name"):
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor() or "unknown"


def _cpu_model_darwin() -> str:
    try:
        result = subprocess.run(
            ["sysctl", "-n", "machdep.cpu.brand_string"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    return platform.processor() or "unknown"


def _physical_cpu_count() -> int | None:
    try:
        import multiprocessing

        return multiprocessing.cpu_count()
    except Exception:  # noqa: BLE001
        return os.cpu_count()


def _memory_gb() -> float | None:
    system = platform.system()
    if system == "Linux":
        return _memory_gb_linux()
    if system == "Darwin":
        return _memory_gb_darwin()
    return None


def _memory_gb_linux() -> float | None:
    try:
        text = Path("/proc/meminfo").read_text()
        for line in text.splitlines():
            if line.startswith("MemTotal:"):
                kb = int(line.split()[1])
                return round(kb / (1024 * 1024), 1)
    except (OSError, ValueError, IndexError):
        pass
    return None


def _memory_gb_darwin() -> float | None:
    try:
        result = subprocess.run(
            ["sysctl", "-n", "hw.memsize"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return round(int(result.stdout.strip()) / (1024**3), 1)
    except (OSError, subprocess.TimeoutExpired, ValueError):
        pass
    return None


def _gpu_info() -> list[str]:
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total,driver_version",
                "--format=csv,noheader",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            return [line.strip() for line in result.stdout.strip().splitlines() if line.strip()]
    except (OSError, subprocess.TimeoutExpired):
        pass
    return []


def _compiler_version() -> str:
    try:
        result = subprocess.run(
            ["gcc", "--version"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip().splitlines()[0]
    except (OSError, subprocess.TimeoutExpired):
        pass
    return ""
