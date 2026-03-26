"""Project scaffolding for new autoforge projects."""

from __future__ import annotations

import re
from pathlib import Path

from autoforge.pointer import REPO_ROOT, save_pointer

PROJECT_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
PROJECT_SUBDIRS = ("builds", "deploys", "tests", "perfs", "judges", "sprints")


def validate_project_name(name: str) -> None:
    """Raise ValueError if name isn't lowercase alphanumeric + hyphens."""
    if not PROJECT_NAME_RE.match(name):
        msg = f"Invalid project name {name!r}. Must be lowercase alphanumeric + hyphens."
        raise ValueError(msg)


def init_project(name: str) -> Path:
    """Create a new project directory skeleton.

    Creates projects/<name>/ with subdirectories for plugins and sprints.
    Sets the project in .autoforge.toml (sprint left empty).

    Args:
        name: Project name (lowercase alphanumeric + hyphens).

    Returns:
        Path to the created project directory.
    """
    validate_project_name(name)

    project_dir = REPO_ROOT / "projects" / name
    if project_dir.exists():
        msg = f"Project directory already exists: {project_dir}"
        raise FileExistsError(msg)

    for subdir in PROJECT_SUBDIRS:
        (project_dir / subdir).mkdir(parents=True)

    # Set project in pointer (sprint empty until first sprint init)
    save_pointer(name, "")

    return project_dir
