"""Configuration validation (doctor) for autoforge setup."""

from __future__ import annotations

import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from autoforge.config import load_toml_with_local
from autoforge.plugins.loader import CATEGORY_MAP
from autoforge.pointer import REPO_ROOT

if TYPE_CHECKING:
    from autoforge.campaign import CampaignConfig

StatusLevel = Literal["pass", "warn", "fail"]

ICONS: dict[StatusLevel, str] = {"pass": "OK", "warn": "WARN", "fail": "FAIL"}

VALID_PHASES = {"all", "build", "deploy", "test"}
VALID_DIRECTIONS = {"maximize", "minimize"}

# Deploy plugins that are known pass-throughs (no config needed)
PASSTHROUGH_DEPLOYS = {"local"}


@dataclass
class CheckResult:
    """Single configuration check outcome."""

    name: str
    status: StatusLevel
    message: str
    layer: str
    path: str = ""


def _rel(path: Path, root: Path) -> str:
    """Return path relative to root for display."""
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _load_toml(path: Path) -> tuple[dict[str, Any] | None, str]:
    """Load a TOML file, returning (data, error_message)."""
    try:
        with open(path, "rb") as f:
            return tomllib.load(f), ""
    except tomllib.TOMLDecodeError as exc:
        return None, f"invalid TOML: {exc}"
    except OSError as exc:
        return None, f"read error: {exc}"


def _check_toml_file(
    path: Path,
    layer: str,
    name_prefix: str,
    root: Path = REPO_ROOT,
) -> tuple[dict[str, Any] | None, list[CheckResult]]:
    """Load a TOML file and return parsed data plus file-exists/valid-toml check results."""
    results: list[CheckResult] = []
    rel = _rel(path, root)

    if not path.is_file():
        results.append(
            CheckResult(
                f"{name_prefix}.file_exists",
                "fail",
                f"{rel} not found",
                layer,
                path=rel,
            )
        )
        return None, results
    results.append(
        CheckResult(
            f"{name_prefix}.file_exists",
            "pass",
            rel,
            layer,
            path=rel,
        )
    )

    data, err = _load_toml(path)
    if data is None:
        results.append(
            CheckResult(
                f"{name_prefix}.valid_toml",
                "fail",
                f"{rel}: {err}",
                layer,
                path=rel,
            )
        )
        return None, results
    results.append(
        CheckResult(
            f"{name_prefix}.valid_toml",
            "pass",
            "parsed successfully",
            layer,
            path=rel,
        )
    )

    return data, results


def _is_git_repo(path: Path) -> bool:
    """Check if path is a git repository (regular or submodule)."""
    return (path / ".git").exists()


def check_pointer(root: Path = REPO_ROOT) -> list[CheckResult]:
    """Validate the .autoforge.toml pointer file."""
    pointer_path = root / ".autoforge.toml"
    data, results = _check_toml_file(pointer_path, "pointer", "pointer", root)
    if data is None:
        return results

    rel = _rel(pointer_path, root)
    project = data.get("project", "")
    sprint = data.get("sprint", "")

    if not project:
        results.append(
            CheckResult(
                "pointer.project_field",
                "fail",
                "missing or empty 'project' field",
                "pointer",
                path=rel,
            )
        )
    else:
        results.append(
            CheckResult(
                "pointer.project_field",
                "pass",
                f"project = {project!r}",
                "pointer",
                path=rel,
            )
        )

    if not sprint:
        results.append(
            CheckResult(
                "pointer.sprint_field",
                "fail",
                "missing or empty 'sprint' field",
                "pointer",
                path=rel,
            )
        )
    else:
        results.append(
            CheckResult(
                "pointer.sprint_field",
                "pass",
                f"sprint = {sprint!r}",
                "pointer",
                path=rel,
            )
        )

    if project:
        project_dir = root / "projects" / project
        rel_proj = _rel(project_dir, root)
        if project_dir.is_dir():
            results.append(
                CheckResult(
                    "pointer.project_exists",
                    "pass",
                    f"{rel_proj}/ exists",
                    "pointer",
                    path=rel_proj,
                )
            )
        else:
            results.append(
                CheckResult(
                    "pointer.project_exists",
                    "fail",
                    f"{rel_proj}/ not found",
                    "pointer",
                    path=rel_proj,
                )
            )

    if project and sprint:
        sprint_dir = root / "projects" / project / "sprints" / sprint
        rel_sprint = _rel(sprint_dir, root)
        if sprint_dir.is_dir():
            results.append(
                CheckResult(
                    "pointer.sprint_exists",
                    "pass",
                    f"{rel_sprint}/ exists",
                    "pointer",
                    path=rel_sprint,
                )
            )
        else:
            results.append(
                CheckResult(
                    "pointer.sprint_exists",
                    "fail",
                    f"{rel_sprint}/ not found",
                    "pointer",
                    path=rel_sprint,
                )
            )

    return results


def check_campaign(
    project: str,
    sprint: str,
    root: Path = REPO_ROOT,
) -> list[CheckResult]:
    """Validate the campaign TOML for the active sprint."""
    path = root / "projects" / project / "sprints" / sprint / "campaign.toml"
    data, results = _check_toml_file(path, "campaign", "campaign", root)
    if data is None:
        return results

    rel = _rel(path, root)

    for section in ("campaign", "metric", "project"):
        if section in data:
            results.append(
                CheckResult(
                    f"campaign.section_{section}",
                    "pass",
                    f"[{section}] present",
                    "campaign",
                    path=rel,
                )
            )
        else:
            results.append(
                CheckResult(
                    f"campaign.section_{section}",
                    "fail",
                    f"[{section}] section missing",
                    "campaign",
                    path=rel,
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
                path=rel,
            )
        )
    elif direction:
        results.append(
            CheckResult(
                "campaign.metric_direction",
                "fail",
                f"expected 'maximize' or 'minimize', got {direction!r}",
                "campaign",
                path=rel,
            )
        )
    else:
        results.append(
            CheckResult(
                "campaign.metric_direction",
                "fail",
                "metric.direction not set",
                "campaign",
                path=rel,
            )
        )

    proj = data.get("project", {})
    for field in ("build", "deploy", "test"):
        val = proj.get(field, "")
        if val:
            results.append(
                CheckResult(
                    f"campaign.project_{field}",
                    "pass",
                    f"project.{field} = {val!r}",
                    "campaign",
                    path=rel,
                )
            )
        else:
            results.append(
                CheckResult(
                    f"campaign.project_{field}",
                    "fail",
                    f"project.{field} not set",
                    "campaign",
                    path=rel,
                )
            )

    submodule = proj.get("submodule_path", "")
    if submodule:
        sub_path = root / submodule
        if not sub_path.is_dir():
            results.append(
                CheckResult(
                    "campaign.submodule_path",
                    "warn",
                    f"{submodule} not found on disk",
                    "campaign",
                    path=submodule,
                )
            )
        elif not _is_git_repo(sub_path):
            results.append(
                CheckResult(
                    "campaign.submodule_path",
                    "warn",
                    f"{submodule} exists but is not a git repository"
                    " (run: git submodule update --init)",
                    "campaign",
                    path=submodule,
                )
            )
        else:
            results.append(
                CheckResult(
                    "campaign.submodule_path",
                    "pass",
                    f"{submodule} is a git repository",
                    "campaign",
                    path=submodule,
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
                path=rel,
            )
        )
    else:
        results.append(
            CheckResult(
                "campaign.project_scope",
                "warn",
                "project.scope is empty or not set",
                "campaign",
                path=rel,
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
                    path=rel,
                )
            )
        else:
            results.append(
                CheckResult(
                    "campaign.profiler_set",
                    "fail",
                    "profiling enabled but project.profiler not set",
                    "campaign",
                    path=rel,
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
    data, results = _check_toml_file(path, "runner", "runner", root)
    if data is None:
        return results

    rel = _rel(path, root)

    paths = data.get("paths", {})
    if paths:
        results.append(
            CheckResult(
                "runner.section_paths",
                "pass",
                "[paths] present",
                "runner",
                path=rel,
            )
        )
    else:
        results.append(
            CheckResult(
                "runner.section_paths",
                "fail",
                "[paths] section missing",
                "runner",
                path=rel,
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
                path=dpdk_src,
            )
        )
    elif dpdk_src:
        results.append(
            CheckResult(
                "runner.dpdk_src",
                "warn",
                f"{dpdk_src} not found on disk",
                "runner",
                path=dpdk_src,
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
                path=rel,
            )
        )
    else:
        results.append(
            CheckResult(
                "runner.timeouts",
                "fail",
                "[timeouts] missing build_minutes or test_minutes",
                "runner",
                path=rel,
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
                path=rel,
            )
        )
    else:
        results.append(
            CheckResult(
                "runner.phase",
                "fail",
                f"invalid phase {phase!r}, expected one of {sorted(VALID_PHASES)}",
                "runner",
                path=rel,
            )
        )

    return results


def _check_config_sections(
    toml_path: Path,
    category: str,
    rel_toml: str,
    root: Path,
) -> list[CheckResult]:
    """Warn about unexpected top-level sections in a local override.

    Compares the ``.local.toml`` override's top-level keys against
    the shared base ``.toml`` file. Keys present in the local
    override but absent from the base are flagged as warnings.
    """
    results: list[CheckResult] = []

    local_path = toml_path.with_suffix(".local.toml")
    if not local_path.is_file() or not toml_path.is_file():
        return results

    local_data, _ = _load_toml(local_path)
    base_data, _ = _load_toml(toml_path)
    if local_data is None or base_data is None:
        return results

    expected_sections = set(base_data.keys())
    actual_sections = set(local_data.keys())
    unexpected = actual_sections - expected_sections

    rel_local = _rel(local_path, root)
    for section in sorted(unexpected):
        results.append(
            CheckResult(
                f"plugin.{category}.config_sections",
                "warn",
                f"{rel_local}: unexpected section [{section}] "
                f"(expected: {sorted(expected_sections)})",
                "plugin",
                path=rel_local,
            )
        )

    return results


def check_plugins(
    project: str,
    campaign_data: CampaignConfig,
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
        rel_py = _rel(py_path, root)
        rel_toml = _rel(toml_path, root)

        if py_path.is_file():
            results.append(
                CheckResult(
                    f"plugin.{category}.file_exists",
                    "pass",
                    f"{rel_py}",
                    "plugin",
                    path=rel_py,
                )
            )
        else:
            results.append(
                CheckResult(
                    f"plugin.{category}.file_exists",
                    "fail",
                    f"{rel_py} not found",
                    "plugin",
                    path=rel_py,
                )
            )

        local_path = plugin_dir / f"{name}.local.toml"
        rel_local = _rel(local_path, root)

        if toml_path.is_file():
            data, err = _load_toml(toml_path)
            if data is not None:
                results.append(
                    CheckResult(
                        f"plugin.{category}.config_valid",
                        "pass",
                        rel_toml,
                        "plugin",
                        path=rel_toml,
                    )
                )
                if local_path.is_file():
                    results.append(
                        CheckResult(
                            f"plugin.{category}.local_override",
                            "pass",
                            f"{rel_local} (local overrides active)",
                            "plugin",
                            path=rel_local,
                        )
                    )
                results.extend(_check_config_sections(toml_path, category, rel_toml, root))
                merged = load_toml_with_local(toml_path)
                results.extend(_check_sensitive_empty(merged, rel_toml, category))
            else:
                results.append(
                    CheckResult(
                        f"plugin.{category}.config_valid",
                        "fail",
                        f"{rel_toml}: {err}",
                        "plugin",
                        path=rel_toml,
                    )
                )
        elif category == "deploy" and name in PASSTHROUGH_DEPLOYS:
            results.append(
                CheckResult(
                    f"plugin.{category}.config_exists",
                    "pass",
                    f"{name} deployer is a pass-through (no config needed)",
                    "plugin",
                    path=rel_toml,
                )
            )
        else:
            results.append(
                CheckResult(
                    f"plugin.{category}.config_exists",
                    "warn",
                    f"{rel_toml} not found — shared defaults should be tracked in git",
                    "plugin",
                    path=rel_toml,
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
    rel_sprint = _rel(sprint_dir, root)

    requests = sprint_dir / "requests"
    rel_req = f"{rel_sprint}/requests"
    if requests.is_dir():
        results.append(
            CheckResult(
                "sprint.requests_dir",
                "pass",
                f"{rel_req}/ exists",
                "sprint",
                path=rel_req,
            )
        )
    else:
        results.append(
            CheckResult(
                "sprint.requests_dir",
                "fail",
                f"{rel_req}/ missing",
                "sprint",
                path=rel_req,
            )
        )

    results_tsv = sprint_dir / "results.tsv"
    rel_tsv = f"{rel_sprint}/results.tsv"
    if results_tsv.is_file():
        results.append(
            CheckResult(
                "sprint.results_tsv",
                "pass",
                rel_tsv,
                "sprint",
                path=rel_tsv,
            )
        )
    else:
        results.append(
            CheckResult(
                "sprint.results_tsv",
                "warn",
                f"{rel_tsv} not found (created on first result)",
                "sprint",
                path=rel_tsv,
            )
        )

    return results


def _collect_effective_config(
    project: str,
    sprint: str,
    role: str,
    root: Path,
) -> dict[str, Any]:
    """Collect the effective merged configuration for display."""
    config: dict[str, Any] = {
        "project": project,
        "sprint": sprint,
    }

    # Campaign
    campaign_path = root / "projects" / project / "sprints" / sprint / "campaign.toml"
    if campaign_path.is_file():
        data, _ = _load_toml(campaign_path)
        if data:
            proj = data.get("project", {})
            config["plugins"] = {
                "build": proj.get("build", ""),
                "deploy": proj.get("deploy", ""),
                "test": proj.get("test", ""),
                "profiler": proj.get("profiler", ""),
            }
            config["metric"] = data.get("metric", {})
            config["platform"] = data.get("platform", {})
            config["profiling"] = data.get("profiling", {})
            config["submodule_path"] = proj.get("submodule_path", "")
            config["scope"] = proj.get("scope", [])

    # Runner (only if not agent-only) — merge shared + local overrides
    if role != "agent":
        runner_path = root / "projects" / project / "runner.toml"
        data = load_toml_with_local(runner_path)
        if data:
            config["runner"] = data.get("runner", {})
            config["paths"] = data.get("paths", {})
            config["timeouts"] = data.get("timeouts", {})

    # Plugin configs — merge shared + local overrides
    if "plugins" in config:
        plugin_configs: dict[str, dict[str, Any]] = {}
        for category, directory in CATEGORY_MAP.items():
            name = config["plugins"].get(category, "")
            if not name:
                continue
            toml_path = root / "projects" / project / directory / f"{name}.toml"
            data = load_toml_with_local(toml_path)
            if data:
                plugin_configs[f"{directory}/{name}.toml"] = data
        if plugin_configs:
            config["plugin_configs"] = plugin_configs

    return config


def _format_config_value(value: Any, indent: int = 0) -> str:
    """Format a config value for display."""
    prefix = "  " * indent
    if isinstance(value, dict):
        if not value:
            return "{}"
        lines = []
        for k, v in value.items():
            formatted = _format_config_value(v, indent + 1)
            if "\n" in formatted:
                lines.append(f"{prefix}  {k}:")
                lines.append(formatted)
            else:
                lines.append(f"{prefix}  {k}: {formatted}")
        return "\n".join(lines)
    if isinstance(value, list):
        if not value:
            return "[]"
        if all(isinstance(v, str) for v in value) and len(value) <= 5:
            return "[" + ", ".join(repr(v) for v in value) + "]"
        lines = []
        for v in value:
            lines.append(f"{prefix}  - {v}")
        return "\n".join(lines)
    return repr(value)


_SENSITIVE_PATTERNS = frozenset({"token", "key", "secret", "password", "credential", "auth"})


def _is_sensitive_key(key: str) -> bool:
    lower = key.lower()
    return any(pat in lower for pat in _SENSITIVE_PATTERNS)


def _redact_config_value(key: str, value: object) -> object:
    """Redact the value if the key name suggests a credential, else recurse into dicts."""
    if _is_sensitive_key(key):
        return "<redacted>"
    if isinstance(value, dict):
        return {k: _redact_config_value(k, v) for k, v in value.items()}
    return value


def _check_sensitive_empty(
    data: dict[str, Any],
    rel_toml: str,
    category: str,
) -> list[CheckResult]:
    """Warn when a sensitive config key is set to an empty string."""
    results: list[CheckResult] = []

    def _walk(d: dict[str, Any], path: str) -> None:
        for k, v in d.items():
            key_path = f"{path}.{k}" if path else k
            if isinstance(v, dict):
                _walk(v, key_path)
            elif _is_sensitive_key(k) and v == "":
                results.append(
                    CheckResult(
                        f"plugin.{category}.config_empty_secret",
                        "warn",
                        f"{rel_toml}: {key_path} is empty"
                        f" — use ${{{{VAR}}}} syntax to read from environment",
                        "plugin",
                        path=rel_toml,
                    )
                )

    _walk(data, "")
    return results


def format_effective_config(config: dict[str, Any]) -> str:
    """Format the effective configuration as a readable summary."""
    lines: list[str] = ["\n[effective config]"]

    lines.append(f"  project: {config.get('project', '?')}")
    lines.append(f"  sprint:  {config.get('sprint', '?')}")

    platform = config.get("platform", {})
    if platform:
        lines.append(f"  arch:    {platform.get('arch', '?')}")

    plugins = config.get("plugins", {})
    if plugins:
        lines.append("  plugins:")
        for category in ("build", "deploy", "test", "profiler"):
            name = plugins.get(category, "")
            if name:
                lines.append(f"    {category}: {name}")

    metric = config.get("metric", {})
    if metric:
        lines.append(
            f"  metric:  {metric.get('name', '?')}"
            f" ({metric.get('direction', '?')},"
            f" threshold={metric.get('threshold', '?')})"
        )

    submodule = config.get("submodule_path", "")
    if submodule:
        lines.append(f"  source:  {submodule}")

    scope = config.get("scope", [])
    if scope:
        lines.append(f"  scope:   {len(scope)} paths")

    profiling = config.get("profiling", {})
    if profiling:
        lines.append(f"  profiling: {'enabled' if profiling.get('enabled') else 'disabled'}")

    runner = config.get("runner", {})
    if runner:
        lines.append(f"  phase:   {runner.get('phase', 'all')}")

    paths = config.get("paths", {})
    if paths:
        lines.append("  paths:")
        for k, v in paths.items():
            lines.append(f"    {k}: {v}")

    timeouts = config.get("timeouts", {})
    if timeouts:
        lines.append(
            f"  timeouts: build={timeouts.get('build_minutes', '?')}m,"
            f" test={timeouts.get('test_minutes', '?')}m"
        )

    plugin_configs = config.get("plugin_configs", {})
    if plugin_configs:
        lines.append("  plugin configs:")
        for file_name, data in plugin_configs.items():
            lines.append(f"    {file_name}:")
            for section, values in data.items():
                if isinstance(values, dict):
                    lines.append(f"      [{section}]")
                    for k, v in values.items():
                        redacted = _redact_config_value(k, v)
                        lines.append(f"        {k}: {redacted!r}")
                else:
                    redacted = _redact_config_value(section, values)
                    lines.append(f"      {section}: {redacted!r}")

    return "\n".join(lines)


def check_optimization_branch(
    project: str,
    sprint: str,
    campaign_data: dict[str, Any],
    root: Path = REPO_ROOT,
) -> list[CheckResult]:
    """Validate the optimization branch setting in campaign config (agent check)."""
    from autoforge.agent.sprint import OPT_BRANCH_RE  # avoid circular at module level

    results: list[CheckResult] = []
    proj = campaign_data.get("project", {})
    branch = proj.get("optimization_branch", "")
    rel = f"projects/{project}/sprints/{sprint}/campaign.toml"

    if not branch:
        results.append(
            CheckResult(
                "campaign.optimization_branch",
                "fail",
                "project.optimization_branch not set — run 'autoforge sprint init <name>'",
                "agent",
                path=rel,
            )
        )
        return results

    if OPT_BRANCH_RE.match(branch):
        results.append(
            CheckResult(
                "campaign.optimization_branch",
                "pass",
                f"optimization_branch = {branch!r}",
                "agent",
                path=rel,
            )
        )
    else:
        results.append(
            CheckResult(
                "campaign.optimization_branch",
                "warn",
                f"optimization_branch {branch!r} does not match expected pattern "
                f"'autoforge/YYYY-MM-DD-slug' — manual override detected",
                "agent",
                path=rel,
            )
        )

    # Advisory: check if the branch exists in the submodule
    submodule = proj.get("submodule_path", "")
    if submodule:
        sub_path = root / submodule
        if sub_path.is_dir() and (sub_path / ".git").exists():
            result = subprocess.run(
                ["git", "-C", str(sub_path), "branch", "--list", branch],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0 and not result.stdout.strip():
                results.append(
                    CheckResult(
                        "campaign.optimization_branch_exists",
                        "warn",
                        f"Branch {branch!r} does not exist yet in submodule "
                        f"(will be created on first submit or loop start)",
                        "agent",
                        path=submodule,
                    )
                )
            elif result.returncode == 0:
                results.append(
                    CheckResult(
                        "campaign.optimization_branch_exists",
                        "pass",
                        f"Branch {branch!r} exists in submodule",
                        "agent",
                        path=submodule,
                    )
                )

    return results


def run_doctor(
    role: str = "all",
    root: Path = REPO_ROOT,
) -> tuple[list[CheckResult], dict[str, Any]]:
    """Run all configuration checks.

    Args:
        role: "agent", "runner", or "all".
        root: Repository root (override for testing).

    Returns:
        Tuple of (check results, effective config dict).
    """
    results: list[CheckResult] = []

    # Layer 1: Pointer
    pointer_results = check_pointer(root)
    results.extend(pointer_results)
    if any(r.status == "fail" for r in pointer_results):
        return results, {}

    # Extract project/sprint from pointer
    pointer_path = root / ".autoforge.toml"
    try:
        with open(pointer_path, "rb") as f:
            pointer = tomllib.load(f)
        project = pointer["project"]
        sprint = pointer["sprint"]
    except (OSError, KeyError, tomllib.TOMLDecodeError) as exc:
        results.append(
            CheckResult(
                "pointer.read",
                "fail",
                f"Failed to read .autoforge.toml: {exc}",
                "pointer",
            )
        )
        return results, {}

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

    # Layer 6: Optimization branch (agent only)
    if campaign_data and role in ("agent", "all"):
        results.extend(check_optimization_branch(project, sprint, campaign_data, root))

    # Collect effective config
    effective = _collect_effective_config(project, sprint, role, root)

    return results, effective


def format_results(
    results: list[CheckResult],
    effective_config: dict[str, Any] | None = None,
) -> str:
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

    if effective_config:
        lines.append(format_effective_config(effective_config))

    return "\n".join(lines)
