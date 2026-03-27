"""vLLM container builder — pull prebuilt or build from source."""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from autoforge.plugins.protocols import BuildResult

if TYPE_CHECKING:
    from autoforge.campaign import ProjectConfig

logger = logging.getLogger(__name__)


def _resolve_runtime(configured: str = "auto") -> str:
    if configured and configured != "auto":
        return configured
    if shutil.which("docker"):
        return "docker"
    if shutil.which("podman"):
        return "podman"
    msg = "No container runtime found. Install docker or podman."
    raise RuntimeError(msg)


class VllmContainerBuilder:
    """Builds a vLLM container image via pull or build (Docker/Podman)."""

    name = "container"

    def configure(self, project_config: ProjectConfig, runner_config: dict[str, Any]) -> None:
        cfg = runner_config.get("build", {})
        self._mode = cfg.get("mode", "prebuilt")
        self._base_image = cfg.get("base_image", "docker.io/vllm/vllm-openai:latest")
        self._local_tag = cfg.get("local_tag", "localhost/vllm-bench:latest")
        self._dockerfile = cfg.get("dockerfile", "Dockerfile")
        self._runtime = _resolve_runtime(cfg.get("runtime", "auto"))

    def build(
        self,
        source_path: Path,
        commit: str,
        build_dir: Path,
        timeout: int,
    ) -> BuildResult:
        start = time.monotonic()
        if self._mode == "prebuilt":
            return self._build_prebuilt(start, timeout)
        return self._build_from_source(source_path, commit, start, timeout)

    def _build_prebuilt(self, start: float, timeout: int) -> BuildResult:
        try:
            result = subprocess.run(
                [self._runtime, "pull", self._base_image],
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            elapsed = time.monotonic() - start
            if result.returncode != 0:
                logger.error("%s pull failed: %s", self._runtime, result.stderr.strip())
                return BuildResult(
                    success=False,
                    log=result.stderr[-2000:],
                    duration_seconds=elapsed,
                )
            subprocess.run(
                [self._runtime, "tag", self._base_image, self._local_tag],
                check=True,
                capture_output=True,
                timeout=30,
            )
            logger.info("Pulled %s in %.1fs", self._base_image, elapsed)
            return BuildResult(
                success=True,
                log=f"Pulled {self._base_image}",
                duration_seconds=elapsed,
                artifacts={"image": self._local_tag, "mode": "prebuilt"},
            )
        except subprocess.TimeoutExpired:
            return BuildResult(
                success=False,
                log="TIMEOUT pulling image",
                duration_seconds=time.monotonic() - start,
            )

    @staticmethod
    def _get_scm_version(source_path: Path) -> str:
        """Get version string from git describe for setuptools_scm override."""
        result = subprocess.run(
            ["git", "describe", "--tags"],
            cwd=source_path,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            return result.stdout.strip().lstrip("v")
        # No reachable tag — fall back to a valid PEP 440 version with
        # the short commit hash so the build doesn't fail.
        rev = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=source_path,
            capture_output=True,
            text=True,
            timeout=10,
        )
        short_hash = rev.stdout.strip() if rev.returncode == 0 else "unknown"
        return f"0.0.0.dev0+g{short_hash}"

    def _patch_dockerfile_for_submodule(self, source_path: Path, version: str) -> None:
        """Patch Dockerfile to eliminate all .git access during build.

        In submodules, .git is a pointer file, not a directory. Docker
        bind mounts expose this file as-is, breaking setuptools_scm and
        vcs_versioning. Fix by:
        1. Removing all .git bind mounts from RUN instructions
        2. Overriding SETUPTOOLS_SCM_PRETEND_VERSION in every stage
        3. Adding .git to .dockerignore to exclude from build context
        """
        dockerfile = source_path / self._dockerfile
        content = dockerfile.read_text()

        # Remove .git bind mounts (they can't resolve submodule pointers).
        # Case 1: .git mount as a continuation line (e.g. after --mount=type=cache).
        content = re.sub(
            r"\n\s*--mount=type=bind,source=\.git,target=\.git\s*\\",
            "",
            content,
        )
        # Case 2: RUN whose only purpose is a .git mount (GIT_REPO_CHECK).
        content = re.sub(
            r"RUN --mount=type=bind,source=\.git,target=\.git\s*\\\n"
            r"\s*if \[ .+?; fi\n",
            "",
            content,
        )

        # Override the csrc-build stage's pretend version (may be ignored
        # by newer vcs_versioning) with VLLM_VERSION_OVERRIDE which
        # setup.py checks before calling setuptools_scm.
        content = content.replace(
            'ENV SETUPTOOLS_SCM_PRETEND_VERSION="0.0.0+csrc.build"',
            'ENV SETUPTOOLS_SCM_PRETEND_VERSION="0.0.0+csrc.build"\n'
            'ENV VLLM_VERSION_OVERRIDE="0.0.0+csrc.build"',
        )

        # Inject version into the build stage
        content = content.replace(
            "ENV VLLM_SKIP_PRECOMPILED_VERSION_SUFFIX=1",
            "ENV VLLM_SKIP_PRECOMPILED_VERSION_SUFFIX=1\n"
            f'ENV SETUPTOOLS_SCM_PRETEND_VERSION="{version}"\n'
            f'ENV VLLM_VERSION_OVERRIDE="{version}"',
        )

        dockerfile.write_text(content)

        # Exclude .git from build context entirely
        dockerignore = source_path / ".dockerignore"
        ignore_text = dockerignore.read_text() if dockerignore.exists() else ""
        if ".git" not in ignore_text.splitlines():
            dockerignore.write_text(ignore_text.rstrip() + "\n.git\n")

        logger.info("Patched Dockerfile: removed .git mounts, set version=%s", version)

    def _build_from_source(
        self,
        source_path: Path,
        commit: str,
        start: float,
        timeout: int,
    ) -> BuildResult:
        try:
            subprocess.run(
                ["git", "checkout", commit],
                cwd=source_path,
                check=True,
                capture_output=True,
                timeout=30,
            )
            # Patch Dockerfile to eliminate .git access (broken in submodules)
            version = self._get_scm_version(source_path)
            self._patch_dockerfile_for_submodule(source_path, version)

            cmd = [self._runtime, "build"]
            if self._runtime == "podman":
                cmd.extend(["--security-opt", "label=disable"])
            cmd.extend(
                [
                    "--build-arg",
                    "VLLM_USE_PRECOMPILED=1",
                    "--build-arg",
                    f"VLLM_MERGE_BASE_COMMIT={commit}",
                    "-t",
                    self._local_tag,
                    "-f",
                    str(source_path / self._dockerfile),
                    str(source_path),
                ]
            )
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            elapsed = time.monotonic() - start
            if result.returncode != 0:
                logger.error("%s build failed (exit %d)", self._runtime, result.returncode)
                return BuildResult(
                    success=False,
                    log=result.stderr[-2000:],
                    duration_seconds=elapsed,
                )
            logger.info("Built from source (%s) in %.1fs", commit[:12], elapsed)
            return BuildResult(
                success=True,
                log=result.stdout[-2000:],
                duration_seconds=elapsed,
                artifacts={
                    "image": self._local_tag,
                    "mode": "source",
                    "commit": commit,
                },
            )
        except subprocess.TimeoutExpired:
            return BuildResult(
                success=False,
                log="TIMEOUT building image",
                duration_seconds=time.monotonic() - start,
            )
