"""vLLM container builder — pull prebuilt or build from source."""

from __future__ import annotations

import logging
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

    def _inject_scm_version(self, source_path: Path, version: str) -> None:
        """Inject SETUPTOOLS_SCM_PRETEND_VERSION into Dockerfile build stage.

        In submodules, .git is a pointer file, not a directory. Docker
        bind mounts can't follow this, so setuptools_scm fails during
        wheel build. Injecting the version ENV bypasses git entirely.

        Only targets the final 'build' stage marker (preceded by
        VLLM_SKIP_PRECOMPILED_VERSION_SUFFIX), not the csrc-build stage
        which already sets its own SETUPTOOLS_SCM_PRETEND_VERSION.
        """
        dockerfile = source_path / self._dockerfile
        content = dockerfile.read_text()
        # Use the unique context around the build stage's wheel-build comment
        # to avoid injecting into the csrc-build stage.
        marker = "ENV VLLM_SKIP_PRECOMPILED_VERSION_SUFFIX=1"
        if marker not in content:
            logger.warning("Dockerfile marker not found, skipping SCM injection")
            return
        env_line = f'\nENV SETUPTOOLS_SCM_PRETEND_VERSION="{version}"'
        content = content.replace(marker, marker + env_line)
        dockerfile.write_text(content)
        logger.info("Injected SETUPTOOLS_SCM_PRETEND_VERSION=%s into Dockerfile", version)

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
            # Inject version so setuptools_scm doesn't need .git access
            version = self._get_scm_version(source_path)
            self._inject_scm_version(source_path, version)

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
