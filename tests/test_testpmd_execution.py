"""Tests for testpmd-memif.py execution paths (PTY, process management)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

PLUGIN_PATH = Path(__file__).parent.parent / "projects" / "dpdk" / "tests" / "testpmd-memif.py"
MODULE_NAME = "testpmd_memif_exec_module"


def _load_testpmd_module():
    spec = importlib.util.spec_from_file_location(MODULE_NAME, PLUGIN_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[MODULE_NAME] = mod
    spec.loader.exec_module(mod)
    return mod


_mod = _load_testpmd_module()
run_testpmd = _mod.run_testpmd
_read_until = _mod._read_until
_ensure_stopped = _mod._ensure_stopped
_find_child_pid = _mod._find_child_pid
TestpmdMemifTester = _mod.TestpmdMemifTester
TestpmdResult = _mod.TestpmdResult


class TestRunTestpmdBinaryNotFound:
    def test_binary_not_found(self, tmp_path: Path) -> None:
        build_dir = tmp_path / "build"
        build_dir.mkdir()
        config: dict = {"testpmd": {}}

        result = run_testpmd(build_dir, config, timeout=10)

        assert result.success is False
        assert result.throughput_mpps is None
        assert "not found" in (result.error or "")

    def test_popen_oserror(self, tmp_path: Path) -> None:
        build_dir = tmp_path / "build"
        (build_dir / "app").mkdir(parents=True)
        (build_dir / "app" / "dpdk-testpmd").touch()
        config: dict = {"testpmd": {"sudo": False}}

        with (
            patch(f"{MODULE_NAME}.pty.openpty", return_value=(10, 11)),
            patch(f"{MODULE_NAME}.subprocess.Popen", side_effect=OSError("exec failed")),
            patch(f"{MODULE_NAME}.os.close") as mock_close,
        ):
            result = run_testpmd(build_dir, config, timeout=10)

        assert result.success is False
        assert "Failed to start" in (result.error or "")
        assert mock_close.call_count == 2


class TestReadUntil:
    def test_finds_marker(self) -> None:
        fd = 99
        chunks = [b"hello\n", b"world\n", b"MARKER done\n"]
        chunk_iter = iter(chunks)

        def fake_read(fileno, size):
            return next(chunk_iter)

        with (
            patch(f"{MODULE_NAME}.select.select", return_value=([fd], [], [])),
            patch(f"{MODULE_NAME}.os.read", side_effect=fake_read),
        ):
            output = _read_until(fd, "MARKER", timeout=5)

        assert "MARKER" in output

    def test_timeout_returns_partial(self) -> None:
        fd = 99
        select_calls = 0

        def fake_select(rlist, wlist, xlist, timeout):
            nonlocal select_calls
            select_calls += 1
            if select_calls > 2:
                return ([], [], [])
            return ([fd], [], [])

        read_calls = 0

        def fake_read(fileno, size):
            nonlocal read_calls
            read_calls += 1
            if read_calls <= 2:
                return b"partial data\n"
            return b""

        with (
            patch(f"{MODULE_NAME}.select.select", side_effect=fake_select),
            patch(f"{MODULE_NAME}.os.read", side_effect=fake_read),
        ):
            output = _read_until(fd, "NEVER_FOUND", timeout=1)

        assert "partial data" in output


class TestEnsureStopped:
    def test_already_exited(self) -> None:
        proc = MagicMock()
        proc.poll.return_value = 0
        fd = 99

        with patch(f"{MODULE_NAME}.os.close"):
            _ensure_stopped(proc, fd)

        proc.kill.assert_not_called()
        proc.wait.assert_not_called()

    def test_graceful_shutdown(self) -> None:
        proc = MagicMock()
        proc.poll.return_value = None
        proc.wait.return_value = 0
        fd = 99

        with patch(f"{MODULE_NAME}.os.write"), patch(f"{MODULE_NAME}.os.close"):
            _ensure_stopped(proc, fd)

        proc.wait.assert_called_once_with(timeout=10)
        proc.kill.assert_not_called()

    def test_kill_on_timeout(self) -> None:
        proc = MagicMock()
        proc.poll.return_value = None
        from subprocess import TimeoutExpired

        proc.wait.side_effect = [TimeoutExpired("cmd", 10), None]
        fd = 99

        with patch(f"{MODULE_NAME}.os.write"), patch(f"{MODULE_NAME}.os.close"):
            _ensure_stopped(proc, fd)

        proc.kill.assert_called_once()


class TestFindChildPid:
    def test_child_found(self) -> None:
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "12345\n"

        with patch(f"{MODULE_NAME}.subprocess.run", return_value=mock_result):
            pid = _find_child_pid(9999)

        assert pid == 12345

    def test_no_child_returns_none(self) -> None:
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""

        with patch(f"{MODULE_NAME}.subprocess.run", return_value=mock_result):
            pid = _find_child_pid(9999)

        assert pid is None

    def test_timeout_returns_none(self) -> None:
        from subprocess import TimeoutExpired

        with patch(
            f"{MODULE_NAME}.subprocess.run",
            side_effect=TimeoutExpired("pgrep", 5),
        ):
            pid = _find_child_pid(9999)

        assert pid is None


class TestTestpmdMemifTesterTest:
    def test_delegates_to_repeated(self) -> None:
        from autoforge.plugins.protocols import DeployResult

        tester = TestpmdMemifTester()
        tester.configure({}, {"profiling": {}})

        mock_result = TestpmdResult(
            success=True,
            throughput_mpps=86.0,
            port_stats="stats output",
            error=None,
            duration_seconds=15.0,
            profile_summary=None,
        )

        deploy = DeployResult(success=True, target_info={"build_dir": "/tmp/build"})

        with patch(
            f"{MODULE_NAME}.run_testpmd_repeated", return_value=mock_result
        ) as mock_repeated:
            result = tester.test(deploy, timeout=600)

        mock_repeated.assert_called_once()
        assert result.success is True
        assert result.metric_value == 86.0
