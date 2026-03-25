"""Plugin discovery via Python entry points."""

from __future__ import annotations

from importlib.metadata import entry_points

from autoforge.plugins.protocols import Plugin

ENTRY_POINT_GROUP = "autoforge.plugins"


def load_plugin(name: str) -> Plugin:
    """Load a plugin by name from installed entry points.

    Args:
        name: The registered plugin name (e.g., "dpdk").

    Returns:
        An instantiated Plugin object.

    Raises:
        ValueError: If the plugin is not found.
    """
    eps = entry_points(group=ENTRY_POINT_GROUP)
    for ep in eps:
        if ep.name == name:
            factory = ep.load()
            return factory()
    installed = [ep.name for ep in eps]
    msg = f"Plugin {name!r} not found. Installed plugins: {installed}"
    raise ValueError(msg)


def list_plugins() -> list[str]:
    """Return names of all installed plugins."""
    return [ep.name for ep in entry_points(group=ENTRY_POINT_GROUP)]
