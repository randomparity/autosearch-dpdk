"""File-based plugin discovery from projects/<name>/{builds,deploys,tests,perfs}/."""

from __future__ import annotations

import importlib.util
import inspect
import logging
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from autoforge.campaign import CampaignConfig, project_config
from autoforge.plugins.protocols import (
    Builder,
    Deployer,
    Judge,
    Profiler,
    Tester,
)

logger = logging.getLogger(__name__)

PROJECTS_ROOT = Path(__file__).resolve().parent.parent.parent / "projects"

CATEGORY_MAP: dict[str, str] = {
    "build": "builds",
    "deploy": "deploys",
    "test": "tests",
    "profiler": "perfs",
    "judge": "judges",
}

CATEGORY_PROTOCOLS: dict[str, type] = {
    "build": Builder,
    "deploy": Deployer,
    "test": Tester,
    "profiler": Profiler,
    "judge": Judge,
}

ComponentType = Builder | Deployer | Tester | Profiler | Judge


@dataclass
class PipelineComponents:
    """All plugin components needed for a pipeline run."""

    builder: Builder
    deployer: Deployer
    tester: Tester
    profiler: Profiler | None


def _find_plugin_file(project: str, category: str, name: str, root: Path | None = None) -> Path:
    """Locate the plugin file on disk.

    Args:
        project: Project name (directory under projects/).
        category: One of 'build', 'deploy', 'test', 'profiler', 'judge'.
        name: Plugin name (filename stem, e.g. 'local').
        root: Override projects root (for testing).

    Returns:
        Path to the plugin file.

    Raises:
        ValueError: If the category is invalid.
        FileNotFoundError: If the plugin file does not exist.
    """
    if category not in CATEGORY_MAP:
        msg = f"Invalid category {category!r}, must be one of {sorted(CATEGORY_MAP)}"
        raise ValueError(msg)

    projects = root or PROJECTS_ROOT
    category_dir = projects / project / CATEGORY_MAP[category]
    plugin_path = category_dir / f"{name}.py"

    if not plugin_path.is_file():
        available = list_components(project, category, root=projects)
        msg = f"Plugin {name!r} not found in {category_dir}. Available: {available}"
        raise FileNotFoundError(msg)

    return plugin_path


def _load_python_class(path: Path, protocol: type) -> type | None:
    """Import a Python file and find the first class conforming to protocol.

    Args:
        path: Path to the .py file.
        protocol: The protocol type to match against.

    Returns:
        The matching class, or None if no conforming class found.
    """
    path_hash = hash(str(path.resolve())) % (10**8)
    module_name = f"autoforge_plugin_{path.stem.replace('-', '_')}_{path_hash}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        return None

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        del sys.modules[module_name]
        msg = f"Failed to load plugin from {path}: {exc}"
        raise ValueError(msg) from exc

    for _name, obj in inspect.getmembers(module, inspect.isclass):
        if obj.__module__ != module_name:
            continue
        if isinstance(obj, type) and _conforms_to_protocol(obj, protocol):
            return obj

    return None


def _conforms_to_protocol(cls: type, protocol: type) -> bool:
    """Check if a class conforms to a protocol by instantiating and checking."""
    try:
        instance = cls.__new__(cls)
        return isinstance(instance, protocol)
    except TypeError:
        return False


def load_plugin_config(plugin_path: Path) -> dict[str, Any]:
    """Load the sibling .toml config file for a plugin.

    Looks for a .toml file with the same stem as the plugin .py file
    (e.g. ``local.toml`` next to ``local.py``).

    Returns:
        Config dict, or empty dict if the sibling file does not exist.
    """
    config_path = plugin_path.with_suffix(".toml")
    if not config_path.is_file():
        return {}
    with open(config_path, "rb") as f:
        return tomllib.load(f)


def load_component(
    project: str,
    category: str,
    name: str,
    root: Path | None = None,
    *,
    project_config: dict[str, Any] | None = None,
    runner_config: dict[str, Any] | None = None,
) -> ComponentType:
    """Load a single plugin component by project/category/name.

    When ``runner_config`` is provided, the loader also:
    1. Loads the sibling ``.toml`` config next to the plugin file
    2. Merges it over the framework config (plugin sections win)
    3. Calls ``configure()`` on the instance before returning it

    Args:
        project: Project name (directory under projects/).
        category: One of 'build', 'deploy', 'test', 'profiler', 'judge'.
        name: Plugin name (filename stem).
        root: Override projects root (for testing).
        project_config: Campaign project config (passed to configure).
        runner_config: Framework runner config (merged with sibling .toml).

    Returns:
        An instantiated (and optionally configured) plugin component.

    Raises:
        ValueError: If the category is invalid or no conforming class found.
        FileNotFoundError: If the plugin file does not exist.
    """
    plugin_path = _find_plugin_file(project, category, name, root=root)
    protocol = CATEGORY_PROTOCOLS[category]

    cls = _load_python_class(plugin_path, protocol)
    if cls is None:
        msg = (
            f"No class conforming to {protocol.__name__} found in {plugin_path}. "
            f"The file must contain a class with 'name', 'configure', and the "
            f"required method for the {category} protocol."
        )
        raise ValueError(msg)

    instance = cls()
    logger.info("Loaded %s plugin %r from %s", category, name, plugin_path)

    if runner_config is not None:
        plugin_cfg = load_plugin_config(plugin_path)
        merged = {**runner_config, **plugin_cfg}
        instance.configure(project_config or {}, merged)

    return instance


def list_components(
    project: str,
    category: str,
    root: Path | None = None,
) -> list[str]:
    """List available plugin names for a project/category.

    Args:
        project: Project name.
        category: One of 'build', 'deploy', 'test', 'profiler', 'judge'.
        root: Override projects root (for testing).

    Returns:
        List of plugin names (filename stems).
    """
    if category not in CATEGORY_MAP:
        msg = f"Invalid category {category!r}, must be one of {sorted(CATEGORY_MAP)}"
        raise ValueError(msg)

    projects = root or PROJECTS_ROOT
    category_dir = projects / project / CATEGORY_MAP[category]

    if not category_dir.is_dir():
        return []

    return sorted(p.stem for p in category_dir.glob("*.py") if p.is_file())


def load_judge(
    project: str,
    name: str,
    root: Path | None = None,
    *,
    project_config: dict[str, Any] | None = None,
    runner_config: dict[str, Any] | None = None,
) -> Judge:
    """Load a judge plugin by project and name.

    Args:
        project: Project name (directory under projects/).
        name: Plugin name (filename stem).
        root: Override projects root (for testing).
        project_config: Campaign project config (passed to configure).
        runner_config: Framework runner config (merged with sibling .toml).

    Returns:
        An instantiated (and optionally configured) Judge plugin.
    """
    return load_component(  # type: ignore[return-value]
        project,
        "judge",
        name,
        root=root,
        project_config=project_config,
        runner_config=runner_config,
    )


def load_pipeline(
    project: str,
    campaign: CampaignConfig,
    root: Path | None = None,
) -> PipelineComponents:
    """Load all components specified in a campaign config.

    Args:
        project: Project name.
        campaign: Campaign configuration dict with [project] section.
        root: Override projects root (for testing).

    Returns:
        PipelineComponents with all loaded components.

    Raises:
        ValueError: If required plugins are missing from config.
    """
    project_cfg = project_config(campaign)

    build_name = project_cfg.get("build")
    deploy_name = project_cfg.get("deploy")
    test_name = project_cfg.get("test")
    profiler_name = project_cfg.get("profiler")

    if not build_name:
        msg = "Campaign config missing [project] build plugin name"
        raise ValueError(msg)
    if not deploy_name:
        msg = "Campaign config missing [project] deploy plugin name"
        raise ValueError(msg)
    if not test_name:
        msg = "Campaign config missing [project] test plugin name"
        raise ValueError(msg)

    builder = load_component(project, "build", build_name, root=root)
    deployer = load_component(project, "deploy", deploy_name, root=root)
    tester = load_component(project, "test", test_name, root=root)

    profiler = None
    if profiler_name:
        profiler = load_component(project, "profiler", profiler_name, root=root)

    return PipelineComponents(
        builder=builder,
        deployer=deployer,
        tester=tester,
        profiler=profiler,
    )
