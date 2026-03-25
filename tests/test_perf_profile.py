"""Tests for src.perf.profile and src.perf.arch."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from src.perf.arch import COMMON_EVENTS, detect_arch, load_arch_profile
from src.perf.profile import ProfileResult, fold_stacks, parse_perf_stat, write_folded

# -- Realistic perf script output for testing --

SAMPLE_PERF_SCRIPT = """\
testpmd 12345 1234.567890:   10101010 cycles:
\t00007f1234567890 rte_eth_rx_burst+0x20 (/usr/lib/librte_ethdev.so)
\t00007f1234567abc mlx5_rx_burst_vec+0x50 (/usr/lib/librte_mlx5.so)
\t0000000000401234 main+0x100 (testpmd)

testpmd 12345 1234.567891:   10101010 cycles:
\t00007f1234567890 rte_eth_rx_burst+0x20 (/usr/lib/librte_ethdev.so)
\t00007f1234567abc mlx5_rx_burst_vec+0x50 (/usr/lib/librte_mlx5.so)
\t0000000000401234 main+0x100 (testpmd)

testpmd 12345 1234.567892:   10101010 cycles:
\t00007f12345aaaaa rte_memcpy+0x10 (/usr/lib/librte_eal.so)
\t00007f1234567abc mlx5_rx_burst_vec+0x50 (/usr/lib/librte_mlx5.so)
\t0000000000401234 main+0x100 (testpmd)

"""

SAMPLE_PERF_STAT = """\

 Performance counter stats for process id '12345':

     89,400,000,000      cycles
     71,200,000,000      instructions              #    0.80  insn per cycle
        450,000,000      cache-misses              #   12.50% of all cache refs
      3,600,000,000      cache-references
        120,000,000      branch-misses             #    1.50% of all branches

       10.003456789 seconds time elapsed

"""


class TestFoldStacks:
    def test_basic(self):
        stacks = fold_stacks(SAMPLE_PERF_SCRIPT)
        # 2 samples of the same stack, 1 of a different one
        assert len(stacks) == 2
        # The rx_burst stack appears twice
        rx_stack = "main;mlx5_rx_burst_vec;rte_eth_rx_burst"
        assert stacks[rx_stack] == 2
        # The memcpy stack appears once
        memcpy_stack = "main;mlx5_rx_burst_vec;rte_memcpy"
        assert stacks[memcpy_stack] == 1

    def test_empty_input(self):
        assert fold_stacks("") == {}

    def test_no_frames(self):
        output = "testpmd 12345 1234.567890: 10101010 cycles:\n\n"
        assert fold_stacks(output) == {}

    def test_single_frame(self):
        output = (
            "testpmd 12345 1234.0: 10 cycles:\n\t00007f0000000001 some_func+0x10 (/lib/foo.so)\n\n"
        )
        stacks = fold_stacks(output)
        assert "some_func" in stacks
        assert stacks["some_func"] == 1

    def test_no_trailing_newline(self):
        output = (
            "testpmd 12345 1234.0: 10 cycles:\n"
            "\t00007f0000000001 func_a+0x10 (/lib/a.so)\n"
            "\t00007f0000000002 func_b+0x20 (/lib/b.so)"
        )
        stacks = fold_stacks(output)
        assert "func_b;func_a" in stacks


class TestParsePerfStat:
    def test_basic(self):
        counters = parse_perf_stat(SAMPLE_PERF_STAT)
        assert counters["cycles"] == 89_400_000_000
        assert counters["instructions"] == 71_200_000_000
        assert counters["cache-misses"] == 450_000_000
        assert counters["cache-references"] == 3_600_000_000
        assert counters["branch-misses"] == 120_000_000

    def test_empty(self):
        assert parse_perf_stat("") == {}

    def test_no_match_lines(self):
        assert parse_perf_stat("some random text\nanother line\n") == {}


class TestWriteFolded:
    def test_roundtrip(self, tmp_path: Path):
        stacks = {"a;b;c": 10, "d;e": 5, "f": 1}
        path = tmp_path / "test.folded"
        write_folded(stacks, path)

        # Read back and verify
        content = path.read_text()
        lines = [ln for ln in content.strip().split("\n") if ln]
        assert len(lines) == 3

        # Should be sorted by count descending
        first = lines[0].split()
        assert first[0] == "a;b;c"
        assert first[1] == "10"

    def test_empty_stacks(self, tmp_path: Path):
        path = tmp_path / "empty.folded"
        write_folded({}, path)
        assert path.read_text() == ""


class TestDetectArch:
    def test_returns_string(self):
        arch = detect_arch()
        assert isinstance(arch, str)
        assert len(arch) > 0

    @patch("src.perf.arch.platform.machine", return_value="ppc64le")
    def test_mocked_arch(self, _mock):
        assert detect_arch() == "ppc64le"


class TestLoadArchProfile:
    def test_loads_x86_64(self):
        profile = load_arch_profile("x86_64")
        assert profile["arch"] == "x86_64"
        assert "events" in profile
        assert "cycles" in profile["events"]
        assert "heuristics" in profile
        assert len(profile["heuristics"]) > 0

    def test_loads_ppc64le(self):
        profile = load_arch_profile("ppc64le")
        assert profile["arch"] == "ppc64le"
        assert profile["events"]["cycles"] == "pm_run_cyc"

    def test_loads_aarch64(self):
        profile = load_arch_profile("aarch64")
        assert profile["arch"] == "aarch64"

    def test_loads_s390x(self):
        profile = load_arch_profile("s390x")
        assert profile["arch"] == "s390x"

    def test_fallback_for_unknown_arch(self):
        profile = load_arch_profile("riscv64")
        assert profile["arch"] == "riscv64"
        assert set(profile["events"].values()) == set(COMMON_EVENTS)
        assert profile["heuristics"] == []


class TestProfileResult:
    def test_defaults(self):
        result = ProfileResult(success=True)
        assert result.folded_stacks == {}
        assert result.counters == {}
        assert result.error is None
        assert result.duration_seconds == 0.0

    def test_with_data(self):
        result = ProfileResult(
            success=True,
            folded_stacks={"a;b": 5},
            counters={"cycles": 1000.0},
            duration_seconds=10.5,
        )
        assert result.folded_stacks["a;b"] == 5
        assert result.counters["cycles"] == 1000.0


class TestProfilePidMissing:
    @patch("src.perf.profile.shutil.which", return_value=None)
    def test_perf_not_found(self, _mock, tmp_path: Path):
        from src.perf.profile import profile_pid

        result = profile_pid(pid=1234, duration=5, output_dir=tmp_path)
        assert not result.success
        assert "not found" in result.error
