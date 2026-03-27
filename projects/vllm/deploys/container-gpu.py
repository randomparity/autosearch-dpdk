"""vLLM deployer — launch container with NVIDIA GPU passthrough (Docker/Podman)."""

from __future__ import annotations

import logging
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import TYPE_CHECKING, Any

from autoforge.plugins.protocols import BuildResult, DeployResult

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


class ContainerGpuDeployer:
    """Deploys vLLM in a container with GPU passthrough (Docker or Podman)."""

    name = "container-gpu"

    def configure(self, project_config: ProjectConfig, runner_config: dict[str, Any]) -> None:
        cfg = runner_config.get("deploy", {})
        self._runtime = _resolve_runtime(cfg.get("runtime", "auto"))
        self._model = cfg.get("model", "Qwen/Qwen3-0.6B")
        self._port = int(cfg.get("port", 8000))
        self._container_name = cfg.get("container_name", "vllm-bench")
        self._hf_cache = cfg.get("hf_cache", str(Path.home() / ".cache" / "huggingface"))
        self._extra_engine_args: list[str] = cfg.get("engine_args", [])
        self._startup_timeout = int(cfg.get("startup_timeout", 300))
        self._gpu_memory_util = float(cfg.get("gpu_memory_utilization", 0.90))
        self._env_vars: dict[str, str] = cfg.get("env", {})
        self._devices: str = cfg.get("devices", "all")

    def deploy(self, build_result: BuildResult) -> DeployResult:
        image = build_result.artifacts.get("image", "localhost/vllm-bench:latest")

        subprocess.run(
            [self._runtime, "rm", "-f", self._container_name],
            capture_output=True,
            timeout=30,
        )

        cmd = [
            self._runtime,
            "run",
            "-d",
            "--name",
            self._container_name,
        ]
        if self._runtime == "docker":
            gpu_flag = "all" if self._devices == "all" else f"device={self._devices}"
            cmd.extend(["--gpus", gpu_flag])
        elif self._devices == "all":
            cmd.extend(["--device", "nvidia.com/gpu=all"])
        else:
            for dev in self._devices.split(","):
                cmd.extend(["--device", f"nvidia.com/gpu={dev.strip()}"])
        cmd.extend(
            [
                "--ipc=host",
                "-v",
                f"{self._hf_cache}:/root/.cache/huggingface",
                "-p",
                f"{self._port}:8000",
            ]
        )
        for key, val in self._env_vars.items():
            cmd.extend(["--env", f"{key}={val}"])
        cmd.append(image)
        cmd.extend(
            [
                "--model",
                self._model,
                "--gpu-memory-utilization",
                str(self._gpu_memory_util),
            ]
        )
        cmd.extend(self._extra_engine_args)

        try:
            result = subprocess.run(
                cmd,
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
            container_id = result.stdout.strip()

            if not self._wait_healthy():
                logs = subprocess.run(
                    [self._runtime, "logs", "--tail", "50", self._container_name],
                    capture_output=True,
                    text=True,
                    timeout=30,
                ).stdout
                return DeployResult(
                    success=False,
                    error=(
                        f"Server did not become healthy within "
                        f"{self._startup_timeout}s.\n{logs[-1000:]}"
                    ),
                )

            logger.info("Container %s healthy on port %d", self._container_name, self._port)
            return DeployResult(
                success=True,
                target_info={
                    "container_id": container_id,
                    "container_name": self._container_name,
                    "host": "localhost",
                    "port": self._port,
                    "model": self._model,
                    "runtime": self._runtime,
                },
            )
        except subprocess.CalledProcessError as exc:
            return DeployResult(success=False, error=exc.stderr[:500])
        except subprocess.TimeoutExpired:
            return DeployResult(success=False, error=f"{self._runtime} run timed out")

    def _wait_healthy(self) -> bool:
        url = f"http://localhost:{self._port}/health"
        deadline = time.monotonic() + self._startup_timeout
        while time.monotonic() < deadline:
            try:
                req = urllib.request.Request(url)
                with urllib.request.urlopen(req, timeout=5) as resp:
                    if resp.status == 200:
                        return True
            except (urllib.error.URLError, OSError):
                pass
            time.sleep(5)
        return False
