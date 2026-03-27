"""Tests for vLLM project plugins."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from autoforge.plugins import Builder, Deployer, Profiler, Tester
from autoforge.plugins.loader import load_component
from autoforge.plugins.protocols import BuildResult, DeployResult


@pytest.fixture(autouse=True)
def _isolate_plugin_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prevent on-disk .toml files from interfering with test runner_config."""
    monkeypatch.setattr("autoforge.plugins.loader.load_plugin_config", lambda _path: {})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_completed(
    returncode: int = 0, stdout: str = "", stderr: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


def _project_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "projects" / "vllm"


# ---------------------------------------------------------------------------
# Protocol conformance — real plugin files loaded by the framework loader
# ---------------------------------------------------------------------------


class TestProtocolConformance:
    def test_builder_conforms(self) -> None:
        comp = load_component("vllm", "build", "container")
        assert isinstance(comp, Builder)
        assert comp.name == "container"

    def test_deployer_conforms(self) -> None:
        comp = load_component("vllm", "deploy", "container-gpu")
        assert isinstance(comp, Deployer)
        assert comp.name == "container-gpu"

    def test_tester_conforms(self) -> None:
        comp = load_component("vllm", "test", "bench-serving")
        assert isinstance(comp, Tester)
        assert comp.name == "bench-serving"

    def test_profiler_conforms(self) -> None:
        comp = load_component("vllm", "profiler", "nvidia-smi")
        assert isinstance(comp, Profiler)
        assert comp.name == "nvidia-smi"


# ---------------------------------------------------------------------------
# Builder tests
# ---------------------------------------------------------------------------


class TestVllmContainerBuilder:
    def _make_builder(self, mode: str = "prebuilt") -> Any:
        comp = load_component(
            "vllm",
            "build",
            "container",
            project_config={},
            runner_config={
                "build": {"mode": mode, "base_image": "test:latest", "local_tag": "local:test"}
            },
        )
        return comp

    @patch("subprocess.run")
    def test_prebuilt_pull_success(self, mock_run: MagicMock) -> None:
        mock_run.return_value = _make_completed(0, "pulled")
        builder = self._make_builder("prebuilt")
        result = builder.build(Path("/src"), "abc123", Path("/build"), 300)
        assert result.success
        assert result.artifacts["mode"] == "prebuilt"
        assert result.artifacts["image"] == "local:test"

    @patch("subprocess.run")
    def test_prebuilt_pull_failure(self, mock_run: MagicMock) -> None:
        mock_run.return_value = _make_completed(1, stderr="error: not found")
        builder = self._make_builder("prebuilt")
        result = builder.build(Path("/src"), "abc123", Path("/build"), 300)
        assert not result.success
        assert "error" in result.log.lower()

    @patch("subprocess.run")
    def test_prebuilt_timeout(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="pull", timeout=300)
        builder = self._make_builder("prebuilt")
        result = builder.build(Path("/src"), "abc123", Path("/build"), 300)
        assert not result.success
        assert "TIMEOUT" in result.log

    @patch("subprocess.run")
    def test_source_build_success(self, mock_run: MagicMock) -> None:
        mock_run.return_value = _make_completed(0, "built ok")
        builder = self._make_builder("source")
        result = builder.build(Path("/src"), "def456", Path("/build"), 600)
        assert result.success
        assert result.artifacts["mode"] == "source"
        assert result.artifacts["commit"] == "def456"

    @patch("subprocess.run")
    def test_source_build_failure(self, mock_run: MagicMock) -> None:
        # First call (git checkout) succeeds, second (build) fails
        mock_run.side_effect = [
            _make_completed(0),
            _make_completed(1, stderr="build error"),
        ]
        builder = self._make_builder("source")
        result = builder.build(Path("/src"), "bad", Path("/build"), 600)
        assert not result.success


# ---------------------------------------------------------------------------
# Deployer tests
# ---------------------------------------------------------------------------


class TestContainerGpuDeployer:
    def _make_deployer(self, runtime: str = "podman", **overrides: Any) -> Any:
        cfg: dict[str, Any] = {
            "runtime": runtime,
            "model": "test-model",
            "port": 9000,
            "container_name": "test-vllm",
            "startup_timeout": 2,
            "engine_args": [],
            "env": {},
        }
        cfg.update(overrides)
        comp = load_component(
            "vllm",
            "deploy",
            "container-gpu",
            project_config={},
            runner_config={"deploy": cfg},
        )
        return comp

    @patch("urllib.request.urlopen")
    @patch("subprocess.run")
    def test_deploy_success(self, mock_run: MagicMock, mock_urlopen: MagicMock) -> None:
        mock_run.return_value = _make_completed(0, stdout="container123")
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        deployer = self._make_deployer()
        build_result = BuildResult(
            success=True, log="ok", duration_seconds=1.0, artifacts={"image": "test:latest"}
        )
        result = deployer.deploy(build_result)
        assert result.success
        assert result.target_info["port"] == 9000
        assert result.target_info["model"] == "test-model"

    @patch("subprocess.run")
    def test_deploy_run_fails(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = [
            _make_completed(0),  # rm -f
            subprocess.CalledProcessError(1, "run", stderr="gpu error"),
        ]
        deployer = self._make_deployer()
        build_result = BuildResult(
            success=True, log="ok", duration_seconds=1.0, artifacts={"image": "test:latest"}
        )
        result = deployer.deploy(build_result)
        assert not result.success

    @patch("time.sleep")
    @patch("urllib.request.urlopen")
    @patch("subprocess.run")
    def test_deploy_health_check_timeout(
        self,
        mock_run: MagicMock,
        mock_urlopen: MagicMock,
        mock_sleep: MagicMock,
    ) -> None:
        import urllib.error

        mock_run.return_value = _make_completed(0, stdout="cid")
        mock_urlopen.side_effect = urllib.error.URLError("refused")

        deployer = self._make_deployer(startup_timeout=0)
        build_result = BuildResult(
            success=True, log="ok", duration_seconds=1.0, artifacts={"image": "test:latest"}
        )
        result = deployer.deploy(build_result)
        assert not result.success
        assert "healthy" in (result.error or "").lower()

    @patch("urllib.request.urlopen")
    @patch("subprocess.run")
    def test_docker_uses_gpus_all(self, mock_run: MagicMock, mock_urlopen: MagicMock) -> None:
        mock_run.return_value = _make_completed(0, stdout="container123")
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        deployer = self._make_deployer(runtime="docker")
        build_result = BuildResult(
            success=True, log="ok", duration_seconds=1.0, artifacts={"image": "test:latest"}
        )
        deployer.deploy(build_result)

        run_call = mock_run.call_args_list[1]  # second call is 'docker run'
        cmd = run_call.args[0] if run_call.args else run_call.kwargs.get("args", [])
        assert "--gpus" in cmd
        assert "all" in cmd
        assert "--device" not in cmd

    @patch("urllib.request.urlopen")
    @patch("subprocess.run")
    def test_podman_uses_device_nvidia(self, mock_run: MagicMock, mock_urlopen: MagicMock) -> None:
        mock_run.return_value = _make_completed(0, stdout="container123")
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        deployer = self._make_deployer(runtime="podman")
        build_result = BuildResult(
            success=True, log="ok", duration_seconds=1.0, artifacts={"image": "test:latest"}
        )
        deployer.deploy(build_result)

        run_call = mock_run.call_args_list[1]
        cmd = run_call.args[0] if run_call.args else run_call.kwargs.get("args", [])
        assert "--device" in cmd
        assert "nvidia.com/gpu=all" in cmd
        assert "--gpus" not in cmd

    @patch("urllib.request.urlopen")
    @patch("subprocess.run")
    def test_runtime_in_target_info(self, mock_run: MagicMock, mock_urlopen: MagicMock) -> None:
        mock_run.return_value = _make_completed(0, stdout="cid")
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        for rt in ("docker", "podman"):
            deployer = self._make_deployer(runtime=rt)
            build_result = BuildResult(
                success=True, log="ok", duration_seconds=1.0, artifacts={"image": "img"}
            )
            result = deployer.deploy(build_result)
            assert result.success
            assert result.target_info["runtime"] == rt


# ---------------------------------------------------------------------------
# Tester tests
# ---------------------------------------------------------------------------


class TestVllmServingBenchTester:
    def _make_tester(self) -> Any:
        comp = load_component(
            "vllm",
            "test",
            "bench-serving",
            project_config={},
            runner_config={
                "bench": {
                    "num_prompts": 10,
                    "result_dir": "/tmp/test-bench",
                    "bench_cmd": "vllm",
                }
            },
        )
        return comp

    def _deploy_result(self, runtime: str = "podman") -> DeployResult:
        return DeployResult(
            success=True,
            target_info={
                "host": "localhost",
                "port": 8000,
                "model": "test-model",
                "container_name": "test-ctr",
                "runtime": runtime,
            },
        )

    @patch("subprocess.run")
    def test_json_result_parsing(self, mock_run: MagicMock, tmp_path: Path) -> None:
        result_data = {
            "output_throughput": 1234.5,
            "median_ttft_ms": 10.2,
            "median_tpot_ms": 0.5,
        }

        def _side_effect(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
            # When the tester runs "podman cp", write the result file locally
            if len(cmd) >= 3 and cmd[1] == "cp":
                dest = Path(cmd[3]) if len(cmd) > 3 else Path(cmd[-1])
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(json.dumps(result_data))
            return _make_completed(0, stdout="done")

        mock_run.side_effect = _side_effect
        tester = load_component(
            "vllm",
            "test",
            "bench-serving",
            project_config={},
            runner_config={"bench": {"num_prompts": 10}},
        )
        result = tester.test(self._deploy_result(), timeout=60)
        assert result.success
        assert result.metric_value == 1234.5
        assert "1234.5 tok/s" in (result.results_summary or "")

    @patch("subprocess.run")
    def test_regex_fallback(self, mock_run: MagicMock) -> None:
        stdout = "Output token throughput (tok/s): 999.9\nMedian TTFT (ms): 8.1\n"
        mock_run.return_value = _make_completed(0, stdout=stdout)
        tester = load_component(
            "vllm",
            "test",
            "bench-serving",
            project_config={},
            runner_config={"bench": {"num_prompts": 10}},
        )
        result = tester.test(self._deploy_result(), timeout=60)
        assert result.success
        assert result.metric_value == 999.9

    @patch("subprocess.run")
    def test_benchmark_timeout(self, mock_run: MagicMock) -> None:
        # First call (bench) times out, second call (teardown rm -f) succeeds
        mock_run.side_effect = [
            subprocess.TimeoutExpired(cmd="vllm", timeout=60),
            _make_completed(0),
        ]
        tester = self._make_tester()
        result = tester.test(self._deploy_result(), timeout=60)
        assert not result.success
        assert "timed out" in (result.error or "")

    @patch("subprocess.run")
    def test_benchmark_failure(self, mock_run: MagicMock) -> None:
        mock_run.return_value = _make_completed(1, stderr="bench error")
        tester = self._make_tester()
        result = tester.test(self._deploy_result(), timeout=60)
        assert not result.success
        assert "bench error" in (result.error or "")

    @patch("subprocess.run")
    def test_container_teardown(self, mock_run: MagicMock) -> None:
        """Container is removed even after a successful test."""
        mock_run.return_value = _make_completed(0, stdout="done")
        tester = self._make_tester()
        tester.test(self._deploy_result(), timeout=60)
        teardown_calls = [
            c for c in mock_run.call_args_list if "rm" in str(c) and "test-ctr" in str(c)
        ]
        assert len(teardown_calls) >= 1

    @patch("subprocess.run")
    def test_teardown_uses_runtime_from_target_info(self, mock_run: MagicMock) -> None:
        """Teardown uses the runtime from deploy_result.target_info."""
        mock_run.return_value = _make_completed(0, stdout="done")
        tester = self._make_tester()
        tester.test(self._deploy_result(runtime="docker"), timeout=60)
        teardown_calls = [
            c for c in mock_run.call_args_list if "rm" in str(c) and "test-ctr" in str(c)
        ]
        assert len(teardown_calls) >= 1
        teardown_cmd = teardown_calls[0].args[0]
        assert teardown_cmd[0] == "docker"


# ---------------------------------------------------------------------------
# Profiler tests
# ---------------------------------------------------------------------------


class TestNvidiaSmiProfiler:
    def _make_profiler(self) -> Any:
        comp = load_component(
            "vllm",
            "profiler",
            "nvidia-smi",
            project_config={},
            runner_config={"profiling": {"interval_ms": 100}},
        )
        return comp

    @patch("subprocess.run")
    def test_csv_parsing(self, mock_run: MagicMock) -> None:
        csv_output = (
            "2026-03-26 12:00:00.000, 85, 40, 4096, 8192, 65, 250\n"
            "2026-03-26 12:00:01.000, 90, 45, 4200, 8192, 67, 260\n"
        )
        mock_run.return_value = _make_completed(0, stdout=csv_output)
        profiler = self._make_profiler()
        result = profiler.profile(pid=0, duration=10, config={})
        assert result.success
        assert result.summary["num_samples"] == 2
        assert result.summary["avg_gpu_util_pct"] == 87.5
        assert result.summary["max_gpu_util_pct"] == 90

    @patch("subprocess.run")
    def test_timeout_is_success(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="nvidia-smi", timeout=20)
        profiler = self._make_profiler()
        result = profiler.profile(pid=0, duration=10, config={})
        assert result.success
        assert "timeout" in result.summary.get("note", "")

    @patch("subprocess.run")
    def test_nvidia_smi_not_found(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = FileNotFoundError
        profiler = self._make_profiler()
        result = profiler.profile(pid=0, duration=10, config={})
        assert not result.success
        assert "not found" in (result.error or "")

    @patch("subprocess.run")
    def test_empty_output(self, mock_run: MagicMock) -> None:
        mock_run.return_value = _make_completed(0, stdout="")
        profiler = self._make_profiler()
        result = profiler.profile(pid=0, duration=10, config={})
        assert result.success
        assert "error" in result.summary
