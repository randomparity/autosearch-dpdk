"""Tests for testpmd output parsing, vdev validation, and repeated runs."""

from __future__ import annotations

from unittest.mock import patch

from src.runner.testpmd import (
    TestpmdResult,
    _parse_throughput,
    run_testpmd_repeated,
    validate_vdev_config,
)

SAMPLE_TESTPMD_OUTPUT = """\
EAL: Detected 8 lcore(s)
Configuring Port 0 (socket 0)
Configuring Port 1 (socket 0)
Checking link statuses...
Done
testpmd> start tx_first
io packet forwarding - ports=2 - cores=2 - streams=4 - NUMA support enabled

Press enter to exit

  ---------------------- Forward statistics for port 0  ----------------------
  RX-packets: 100000000   RX-dropped: 0             RX-total: 100000000
  TX-packets: 100000000   TX-dropped: 0             TX-total: 100000000
  ---------------------- Forward statistics for port 1  ----------------------
  RX-packets: 100000000   RX-dropped: 0             RX-total: 100000000
  TX-packets: 100000000   TX-dropped: 0             TX-total: 100000000
  ---------------------- Accumulated forward statistics for all ports --------
  RX-packets: 200000000   RX-dropped: 0
  TX-packets: 200000000   TX-dropped: 0
  +++++++++++++++ Accumulated forward statistics for all ports +++++++++++++++
Bye...
"""

SAMPLE_PPS_OUTPUT = """\
Port 0: Rx-pps: 5000000   Tx-pps: 5000000
Port 1: Rx-pps: 5000000   Tx-pps: 5000000
"""


class TestParseThroughput:
    def test_parses_accumulated_stats(self) -> None:
        result = _parse_throughput(SAMPLE_TESTPMD_OUTPUT, duration=15.0)
        assert result is not None
        expected = round(200_000_000 / 15.0 / 1_000_000, 4)
        assert result == expected

    def test_fallback_to_pps(self) -> None:
        result = _parse_throughput(SAMPLE_PPS_OUTPUT, duration=10.0)
        assert result is not None
        assert result == round(10_000_000 / 1_000_000, 4)

    def test_no_data_returns_none(self) -> None:
        result = _parse_throughput("No stats here", duration=10.0)
        assert result is None

    def test_zero_duration_returns_none(self) -> None:
        result = _parse_throughput(SAMPLE_TESTPMD_OUTPUT, duration=0.0)
        assert result is None


class TestValidateVdevConfig:
    def test_no_vdevs(self) -> None:
        assert validate_vdev_config([]) == []

    def test_client_zc_ok(self) -> None:
        assert validate_vdev_config(["net_memif0,role=client,zero-copy=yes"]) == []

    def test_server_zc_warns(self) -> None:
        warnings = validate_vdev_config(["net_memif0,role=server,zero-copy=yes"])
        assert len(warnings) == 1
        assert "server role ignores zero-copy=yes" in warnings[0]

    def test_server_no_zc_ok(self) -> None:
        assert validate_vdev_config(["net_memif0,role=server,zero-copy=no"]) == []

    def test_mixed_pair(self) -> None:
        vdevs = [
            "net_memif0,role=server,id=0,zero-copy=yes",
            "net_memif1,role=client,id=0,zero-copy=yes",
        ]
        warnings = validate_vdev_config(vdevs)
        assert len(warnings) == 1
        assert "server" in warnings[0]


def _make_result(mpps: float, duration: float = 15.0) -> TestpmdResult:
    return TestpmdResult(
        success=True,
        throughput_mpps=mpps,
        port_stats="stats",
        error=None,
        duration_seconds=duration,
    )


def _make_failure(error: str = "boom") -> TestpmdResult:
    return TestpmdResult(
        success=False,
        throughput_mpps=None,
        port_stats=None,
        error=error,
        duration_seconds=1.0,
    )


class TestRunTestpmdRepeated:
    def test_single_run_delegates(self) -> None:
        config = {"testpmd": {"repeat_count": 1}}
        result = _make_result(86.0)
        with patch("src.runner.testpmd.run_testpmd", return_value=result) as mock:
            out = run_testpmd_repeated("/build", config, timeout=600)
        mock.assert_called_once()
        assert out.throughput_mpps == 86.0

    def test_default_repeat_count_delegates(self) -> None:
        config = {"testpmd": {}}
        result = _make_result(86.0)
        with patch("src.runner.testpmd.run_testpmd", return_value=result) as mock:
            out = run_testpmd_repeated("/build", config, timeout=600)
        mock.assert_called_once()
        assert out.throughput_mpps == 86.0

    def test_median_of_three(self) -> None:
        config = {"testpmd": {"repeat_count": 3}}
        results = [_make_result(80.0), _make_result(86.0), _make_result(83.0)]
        with patch("src.runner.testpmd.run_testpmd", side_effect=results):
            out = run_testpmd_repeated("/build", config, timeout=600)
        assert out.throughput_mpps == 83.0
        assert out.success is True

    def test_failure_aborts_early(self) -> None:
        config = {"testpmd": {"repeat_count": 3}}
        results = [_make_result(80.0), _make_failure()]
        with patch("src.runner.testpmd.run_testpmd", side_effect=results) as mock:
            out = run_testpmd_repeated("/build", config, timeout=600)
        assert out.success is False
        assert mock.call_count == 2

    def test_profile_only_last_run(self) -> None:
        config = {"testpmd": {"repeat_count": 3}}
        results = [_make_result(80.0), _make_result(83.0), _make_result(86.0)]
        profile_cfg = {"enabled": True, "frequency": 99}

        calls = []
        orig_results = iter(results)

        def capture_call(build_dir, cfg, timeout, profile_config=None):
            calls.append(profile_config)
            return next(orig_results)

        with patch("src.runner.testpmd.run_testpmd", side_effect=capture_call):
            run_testpmd_repeated("/build", config, timeout=600, profile_config=profile_cfg)

        assert len(calls) == 3
        assert calls[0] is None
        assert calls[1] is None
        assert calls[2] == profile_cfg

    def test_duration_is_sum(self) -> None:
        config = {"testpmd": {"repeat_count": 3}}
        results = [_make_result(80.0, 10.0), _make_result(83.0, 12.0), _make_result(86.0, 11.0)]
        with patch("src.runner.testpmd.run_testpmd", side_effect=results):
            out = run_testpmd_repeated("/build", config, timeout=600)
        assert out.duration_seconds == 33.0
