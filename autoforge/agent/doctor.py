"""Configuration validation (doctor) for autoforge setup."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from autoforge.campaign import REPO_ROOT
from autoforge.plugins.loader import CATEGORY_MAP

StatusLevel = Literal["pass", "warn", "fail"]

ICONS: dict[StatusLevel, str] = {"pass": "OK", "warn": "WARN", "fail": "FAIL"}

VALID_PHASES = {"all", "build", "deploy", "test"}
VALID_DIRECTIONS = {"maximize", "minimize"}


@dataclass
class CheckResult:
    """Single configuration check outcome."""

    name: str
    status: StatusLevel
    message: str
    layer: str


def _load_toml(path: Path) -> tuple[dict[str, Any] | None, str]:
    """Load a TOML file, returning (data, error_message)."""
    try:
        with open(path, "rb") as f:
            return tomllib.load(f), ""
    except tomllib.TOMLDecodeError as exc:
        return None, f"invalid TOML: {exc}"
    except OSError as exc:
        return None, f"read error: {exc}"


def check_pointer(root: Path = REPO_ROOT) -> list[CheckResult]:
    """Validate the .autoforge.toml pointer file."""
    results: list[CheckResult] = []
    pointer_path = root / ".autoforge.toml"

    if not pointer_path.is_file():
        results.append(
            CheckResult(
                "pointer.file_exists",
                "fail",
                ".autoforge.toml not found",
                "pointer",
            )
        )
        return results
    results.append(
        CheckResult(
            "pointer.file_exists",
            "pass",
            ".autoforge.toml found",
            "pointer",
        )
    )

    data, err = _load_toml(pointer_path)
    if data is None:
        results.append(
            CheckResult(
                "pointer.valid_toml",
                "fail",
                err,
                "pointer",
            )
        )
        return results
    results.append(
        CheckResult(
            "pointer.valid_toml",
            "pass",
            "parsed successfully",
            "pointer",
        )
    )

    project = data.get("project", "")
    sprint = data.get("sprint", "")

    if not project:
        results.append(
            CheckResult(
                "pointer.project_field",
                "fail",
                "missing or empty 'project' field",
                "pointer",
            )
        )
    else:
        results.append(
            CheckResult(
                "pointer.project_field",
                "pass",
                f"project = {project!r}",
                "pointer",
            )
        )

    if not sprint:
        results.append(
            CheckResult(
                "pointer.sprint_field",
                "fail",
                "missing or empty 'sprint' field",
                "pointer",
            )
        )
    else:
        results.append(
            CheckResult(
                "pointer.sprint_field",
                "pass",
                f"sprint = {sprint!r}",
                "pointer",
            )
        )

    if project:
        project_dir = root / "projects" / project
        if project_dir.is_dir():
            results.append(
                CheckResult(
                    "pointer.project_exists",
                    "pass",
                    f"projects/{project}/ exists",
                    "pointer",
                )
            )
        else:
            results.append(
                CheckResult(
                    "pointer.project_exists",
                    "fail",
                    f"projects/{project}/ not found",
                    "pointer",
                )
            )

    if project and sprint:
        sprint_dir = root / "projects" / project / "sprints" / sprint
        if sprint_dir.is_dir():
            results.append(
                CheckResult(
                    "pointer.sprint_exists",
                    "pass",
                    f"sprints/{sprint}/ exists",
                    "pointer",
                )
            )
        else:
            results.append(
                CheckResult(
                    "pointer.sprint_exists",
                    "fail",
                    f"sprints/{sprint}/ not found",
                    "pointer",
                )
            )

    return results


def check_campaign(
    project: str,
    sprint: str,
    root: Path = REPO_ROOT,
) -> list[CheckResult]:
    """Validate the campaign TOML for the active sprint."""
    results: list[CheckResult] = []
    path = root / "projects" / project / "sprints" / sprint / "campaign.toml"

    if not path.is_file():
        results.append(
            CheckResult(
                "campaign.file_exists",
                "fail",
                "campaign.toml not found",
                "campaign",
            )
        )
        return results
    results.append(
        CheckResult(
            "campaign.file_exists",
            "pass",
            "campaign.toml found",
            "campaign",
        )
    )

    data, err = _load_toml(path)
    if data is None:
        results.append(
            CheckResult(
                "campaign.valid_toml",
                "fail",
                err,
                "campaign",
            )
        )
        return results
    results.append(
        CheckResult(
            "campaign.valid_toml",
            "pass",
            "parsed successfully",
            "campaign",
        )
    )

    for section in ("campaign", "metric", "project"):
        if section in data:
            results.append(
                CheckResult(
                    f"campaign.section_{section}",
                    "pass",
                    f"[{section}] present",
                    "campaign",
                )
            )
        else:
            results.append(
                CheckResult(
                    f"campaign.section_{section}",
                    "fail",
                    f"[{section}] section missing",
                    "campaign",
                )
            )

    metric = data.get("metric", {})
    direction = metric.get("direction", "")
    if direction in VALID_DIRECTIONS:
        results.append(
            CheckResult(
                "campaign.metric_direction",
                "pass",
                f"direction = {direction!r}",
                "campaign",
            )
        )
    elif direction:
        results.append(
            CheckResult(
                "campaign.metric_direction",
                "fail",
                f"expected 'maximize' or 'minimize', got {direction!r}",
                "campaign",
            )
        )
    else:
        results.append(
            CheckResult(
                "campaign.metric_direction",
                "fail",
                "metric.direction not set",
                "campaign",
            )
        )

    proj = data.get("project", {})
    for field in ("build", "test"):
        val = proj.get(field, "")
        if val:
            results.append(
                CheckResult(
                    f"campaign.project_{field}",
                    "pass",
                    f"project.{field} = {val!r}",
                    "campaign",
                )
            )
        else:
            results.append(
                CheckResult(
                    f"campaign.project_{field}",
                    "fail",
                    f"project.{field} not set",
                    "campaign",
                )
            )

    submodule = proj.get("submodule_path", "")
    if submodule:
        sub_path = root / submodule
        if sub_path.is_dir():
            results.append(
                CheckResult(
                    "campaign.submodule_path",
                    "pass",
                    f"{submodule} exists",
                    "campaign",
                )
            )
        else:
            results.append(
                CheckResult(
                    "campaign.submodule_path",
                    "warn",
                    f"{submodule} not found on disk",
                    "campaign",
                )
            )

    scope = proj.get("scope", [])
    if scope:
        results.append(
            CheckResult(
                "campaign.project_scope",
                "pass",
                f"scope has {len(scope)} entries",
                "campaign",
            )
        )
    else:
        results.append(
            CheckResult(
                "campaign.project_scope",
                "warn",
                "project.scope is empty or not set",
                "campaign",
            )
        )

    profiling = data.get("profiling", {})
    if profiling.get("enabled"):
        profiler = proj.get("profiler", "")
        if profiler:
            results.append(
                CheckResult(
                    "campaign.profiler_set",
                    "pass",
                    f"profiler = {profiler!r}",
                    "campaign",
                )
            )
        else:
            results.append(
                CheckResult(
                    "campaign.profiler_set",
                    "fail",
                    "profiling enabled but project.profiler not set",
                    "campaign",
                )
            )

    return results


def check_runner(
    project: str,
    role: str,
    root: Path = REPO_ROOT,
) -> list[CheckResult]:
    """Validate the runner TOML configuration."""
    results: list[CheckResult] = []

    if role == "agent":
        results.append(
            CheckResult(
                "runner.skipped",
                "pass",
                "runner checks skipped (agent role)",
                "runner",
            )
        )
        return results

    path = root / "projects" / project / "runner.toml"

    if not path.is_file():
        results.append(
            CheckResult(
                "runner.file_exists",
                "fail",
                f"projects/{project}/runner.toml not found (copy from runner.toml.example)",
                "runner",
            )
        )
        return results
    results.append(
        CheckResult(
            "runner.file_exists",
            "pass",
            "runner.toml found",
            "runner",
        )
    )

    data, err = _load_toml(path)
    if data is None:
        results.append(
            CheckResult(
                "runner.valid_toml",
                "fail",
                err,
                "runner",
            )
        )
        return results
    results.append(
        CheckResult(
            "runner.valid_toml",
            "pass",
            "parsed successfully",
            "runner",
        )
    )

    paths = data.get("paths", {})
    if paths:
        results.append(
            CheckResult(
                "runner.section_paths",
                "pass",
                "[paths] present",
                "runner",
            )
        )
    else:
        results.append(
            CheckResult(
                "runner.section_paths",
                "fail",
                "[paths] section missing",
                "runner",
            )
        )

    dpdk_src = paths.get("dpdk_src", "")
    if dpdk_src and Path(dpdk_src).is_dir():
        results.append(
            CheckResult(
                "runner.dpdk_src",
                "pass",
                f"{dpdk_src} exists",
                "runner",
            )
        )
    elif dpdk_src:
        results.append(
            CheckResult(
                "runner.dpdk_src",
                "warn",
                f"{dpdk_src} not found on disk",
                "runner",
            )
        )

    timeouts = data.get("timeouts", {})
    if "build_minutes" in timeouts and "test_minutes" in timeouts:
        results.append(
            CheckResult(
                "runner.timeouts",
                "pass",
                f"build={timeouts['build_minutes']}m, test={timeouts['test_minutes']}m",
                "runner",
            )
        )
    else:
        results.append(
            CheckResult(
                "runner.timeouts",
                "fail",
                "[timeouts] missing build_minutes or test_minutes",
                "runner",
            )
        )

    runner_section = data.get("runner", {})
    phase = runner_section.get("phase", "all")
    if phase in VALID_PHASES:
        results.append(
            CheckResult(
                "runner.phase",
                "pass",
                f"phase = {phase!r}",
                "runner",
            )
        )
    else:
        results.append(
            CheckResult(
                "runner.phase",
                "fail",
                f"invalid phase {phase!r}, expected one of {sorted(VALID_PHASES)}",
                "runner",
            )
        )

    return results


def check_plugins(
    project: str,
    campaign_data: dict[str, Any],
    root: Path = REPO_ROOT,
) -> list[CheckResult]:
    """Validate plugin files referenced by the campaign."""
    results: list[CheckResult] = []
    proj = campaign_data.get("project", {})

    for category, directory in CATEGORY_MAP.items():
        name = proj.get(category, "")
        if not name:
            if category == "profiler":
                continue
            results.append(
                CheckResult(
                    f"plugin.{category}.name",
                    "warn",
                    f"project.{category} not set in campaign",
                    "plugin",
                )
            )
            continue

        plugin_dir = root / "projects" / project / directory
        py_path = plugin_dir / f"{name}.py"
        toml_path = plugin_dir / f"{name}.toml"

        if py_path.is_file():
            results.append(
                CheckResult(
                    f"plugin.{category}.file_exists",
                    "pass",
                    f"{directory}/{name}.py found",
                    "plugin",
                )
            )
        else:
            results.append(
                CheckResult(
                    f"plugin.{category}.file_exists",
                    "fail",
                    f"{directory}/{name}.py not found",
                    "plugin",
                )
            )

        if toml_path.is_file():
            data, err = _load_toml(toml_path)
            if data is not None:
                results.append(
                    CheckResult(
                        f"plugin.{category}.config_valid",
                        "pass",
                        f"{directory}/{name}.toml valid",
                        "plugin",
                    )
                )
            else:
                results.append(
                    CheckResult(
                        f"plugin.{category}.config_valid",
                        "fail",
                        f"{directory}/{name}.toml: {err}",
                        "plugin",
                    )
                )
        else:
            results.append(
                CheckResult(
                    f"plugin.{category}.config_exists",
                    "warn",
                    f"{directory}/{name}.toml not found (plugin may not need config)",
                    "plugin",
                )
            )

    return results


def check_sprint(
    project: str,
    sprint: str,
    root: Path = REPO_ROOT,
) -> list[CheckResult]:
    """Validate sprint directory structure."""
    results: list[CheckResult] = []
    sprint_dir = root / "projects" / project / "sprints" / sprint

    requests = sprint_dir / "requests"
    if requests.is_dir():
        results.append(
            CheckResult(
                "sprint.requests_dir",
                "pass",
                "requests/ exists",
                "sprint",
            )
        )
    else:
        results.append(
            CheckResult(
                "sprint.requests_dir",
                "fail",
                "requests/ directory missing",
                "sprint",
            )
        )

    results_tsv = sprint_dir / "results.tsv"
    if results_tsv.is_file():
        results.append(
            CheckResult(
                "sprint.results_tsv",
                "pass",
                "results.tsv exists",
                "sprint",
            )
        )
    else:
        results.append(
            CheckResult(
                "sprint.results_tsv",
                "warn",
                "results.tsv not found (created on first result)",
                "sprint",
            )
        )

    return results


def run_doctor(
    role: str = "all",
    root: Path = REPO_ROOT,
) -> list[CheckResult]:
    """Run all configuration checks.

    Args:
        role: "agent", "runner", or "all".
        root: Repository root (override for testing).
    """
    results: list[CheckResult] = []

    # Layer 1: Pointer
    pointer_results = check_pointer(root)
    results.extend(pointer_results)
    if any(r.status == "fail" for r in pointer_results):
        return results

    # Extract project/sprint from pointer
    pointer_path = root / ".autoforge.toml"
    with open(pointer_path, "rb") as f:
        pointer = tomllib.load(f)
    project = pointer["project"]
    sprint = pointer["sprint"]

    # Layer 2: Campaign
    campaign_results = check_campaign(project, sprint, root)
    results.extend(campaign_results)

    # Load campaign data for plugin checks (best-effort)
    campaign_path = root / "projects" / project / "sprints" / sprint / "campaign.toml"
    campaign_data: dict[str, Any] = {}
    if campaign_path.is_file():
        data, _ = _load_toml(campaign_path)
        if data is not None:
            campaign_data = data

    # Layer 3: Runner
    results.extend(check_runner(project, role, root))

    # Layer 4: Plugins
    if campaign_data:
        results.extend(check_plugins(project, campaign_data, root))

    # Layer 5: Sprint structure
    results.extend(check_sprint(project, sprint, root))

    return results


def format_results(results: list[CheckResult]) -> str:
    """Format check results as human-readable output."""
    lines: list[str] = []
    current_layer = ""

    for r in results:
        if r.layer != current_layer:
            current_layer = r.layer
            lines.append(f"\n[{current_layer}]")
        icon = ICONS[r.status]
        lines.append(f"  {icon:4s} {r.name}: {r.message}")

    fails = sum(1 for r in results if r.status == "fail")
    warns = sum(1 for r in results if r.status == "warn")
    passes = sum(1 for r in results if r.status == "pass")
    lines.append(f"\n{passes} passed, {warns} warnings, {fails} errors")

    return "\n".join(lines)
