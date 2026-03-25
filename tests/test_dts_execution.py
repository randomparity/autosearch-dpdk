"""Tests for DTS test runner execution paths."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from subprocess import TimeoutExpired
from unittest.mock import MagicMock, patch

PLUGIN_PATH = Path(__file__).parent.parent / "projects" / "dpdk" / "tests" / "dts-mlx5.py"
MODULE_NAME = "dts_mlx5_exec_module"


def _load_dts_module():
    spec = importlib.util.spec_from_file_location(MODULE_NAME, PLUGIN_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[MODULE_NAME] = mod
    spec.loader.exec_module(mod)
    return mod


_mod = _load_dts_module()
run_dts = _mod.run_dts
DtsMlx5Tester = _mod.DtsMlx5Tester
DtsResult = _mod.DtsResult


class TestRunDts:
    def test_success_with_metric(self, tmp_path: Path) -> None:
        dts_path = tmp_path / "dts"
        dts_path.mkdir()
        output_dir = dts_path / "output"
        output_dir.mkdir()
        output_dir.joinpath("results.json").write_text(json.dumps({"throughput_mpps": 22.5}))
        output_dir.joinpath("results_summary.txt").write_text("summary text")

        mock_result = MagicMock()
        mock_result.returncode = 0

        with patch(f"{MODULE_NAME}.subprocess.run", return_value=mock_result):
            result = run_dts(
                dts_path,
                suites=["perf"],
                perf=True,
                metric_path="throughput_mpps",
                timeout=60,
            )

        assert result.success is True
        assert result.metric_value == 22.5
        assert result.results_summary == "summary text"

    def test_nonzero_exit_code(self, tmp_path: Path) -> None:
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "some error"

        with patch(f"{MODULE_NAME}.subprocess.run", return_value=mock_result):
            result = run_dts(
                tmp_path,
                suites=[],
                perf=False,
                metric_path="x",
                timeout=60,
            )

        assert result.success is False
        assert "exited with code 1" in (result.error or "")

    def test_timeout(self, tmp_path: Path) -> None:
        with patch(
            f"{MODULE_NAME}.subprocess.run",
            side_effect=TimeoutExpired("poetry", 60),
        ):
            result = run_dts(
                tmp_path,
                suites=[],
                perf=False,
                metric_path="x",
                timeout=60,
            )

        assert result.success is False
        assert "timed out" in (result.error or "")

    def test_missing_results_files(self, tmp_path: Path) -> None:
        dts_path = tmp_path / "dts"
        dts_path.mkdir()

        mock_result = MagicMock()
        mock_result.returncode = 0

        with patch(f"{MODULE_NAME}.subprocess.run", return_value=mock_result):
            result = run_dts(
                dts_path,
                suites=[],
                perf=False,
                metric_path="x",
                timeout=60,
            )

        assert result.success is True
        assert result.results_json is None
        assert result.results_summary is None
        assert result.metric_value is None

    def test_metric_extraction_failure(self, tmp_path: Path) -> None:
        dts_path = tmp_path / "dts"
        dts_path.mkdir()
        output_dir = dts_path / "output"
        output_dir.mkdir()
        output_dir.joinpath("results.json").write_text(json.dumps({"other_key": 1}))

        mock_result = MagicMock()
        mock_result.returncode = 0

        with patch(f"{MODULE_NAME}.subprocess.run", return_value=mock_result):
            result = run_dts(
                dts_path,
                suites=[],
                perf=False,
                metric_path="nonexistent.path",
                timeout=60,
            )

        assert result.success is True
        assert result.metric_value is None


class TestDtsMlx5Tester:
    def test_configure_stores_config(self) -> None:
        tester = DtsMlx5Tester()
        runner_cfg = {"paths": {"dts_dir": "/opt/dts"}, "test": {}}
        tester.configure({}, runner_cfg)
        assert tester._runner_config == runner_cfg

    def test_delegates_to_run_dts(self) -> None:
        from autoforge.plugins.protocols import DeployResult

        tester = DtsMlx5Tester()
        tester.configure(
            {},
            {
                "paths": {"dts_dir": "/opt/dts"},
                "test": {
                    "test_suites": ["perf"],
                    "perf": True,
                    "metric_path": "throughput_mpps",
                },
            },
        )

        mock_dts_result = DtsResult(
            success=True,
            results_json={"throughput_mpps": 22.5},
            results_summary="summary",
            metric_value=22.5,
            error=None,
            duration_seconds=30.0,
        )

        deploy = DeployResult(success=True, target_info={})

        with patch(f"{MODULE_NAME}.run_dts", return_value=mock_dts_result) as mock_run:
            result = tester.test(deploy, timeout=3600)

        mock_run.assert_called_once()
        assert result.success is True
        assert result.metric_value == 22.5
