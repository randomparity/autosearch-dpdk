"""Tests for metric extraction and comparison."""

from __future__ import annotations

import pytest

from autoforge.agent.metric import compare_metric
from autoforge.protocol import extract_metric

SAMPLE_DTS_RESULT = {
    "test_runs": [
        {
            "test_suites": [
                {
                    "test_cases": [
                        {
                            "name": "test_throughput",
                            "throughput_mpps": 14.7,
                            "latency_us": 2.3,
                        }
                    ],
                    "name": "TestPmd",
                }
            ]
        }
    ],
    "metadata": {
        "dpdk_version": "24.11",
        "timestamp": "2025-01-15T12:00:00",
    },
}


class TestExtractMetric:
    def test_simple_key(self) -> None:
        data = {"throughput": 14.5}
        assert extract_metric(data, "throughput") == 14.5

    def test_non_numeric_value_raises(self) -> None:
        with pytest.raises(ValueError, match="not numeric"):
            extract_metric(SAMPLE_DTS_RESULT, "metadata.timestamp")

    def test_list_index(self) -> None:
        result = extract_metric(
            SAMPLE_DTS_RESULT,
            "test_runs.0.test_suites.0.test_cases.0.throughput_mpps",
        )
        assert result == 14.7

    def test_integer_value(self) -> None:
        data = {"count": 42}
        assert extract_metric(data, "count") == 42

    def test_missing_key_raises(self) -> None:
        with pytest.raises((KeyError, IndexError)):
            extract_metric(SAMPLE_DTS_RESULT, "nonexistent.path")

    def test_missing_nested_key_raises(self) -> None:
        with pytest.raises((KeyError, IndexError)):
            extract_metric(SAMPLE_DTS_RESULT, "test_runs.0.nonexistent")

    def test_index_out_of_range_raises(self) -> None:
        with pytest.raises((KeyError, IndexError)):
            extract_metric(SAMPLE_DTS_RESULT, "test_runs.99")

    def test_empty_path_raises(self) -> None:
        with pytest.raises((KeyError, ValueError)):
            extract_metric(SAMPLE_DTS_RESULT, "")

    def test_deeply_nested(self) -> None:
        data = {"a": {"b": {"c": {"d": 99}}}}
        assert extract_metric(data, "a.b.c.d") == 99


class TestCompareMetric:
    def test_maximize_higher_is_better(self) -> None:
        assert compare_metric(15.0, 14.0, "maximize") is True

    def test_maximize_lower_is_worse(self) -> None:
        assert compare_metric(13.0, 14.0, "maximize") is False

    def test_maximize_equal_is_not_better(self) -> None:
        assert compare_metric(14.0, 14.0, "maximize") is False

    def test_minimize_lower_is_better(self) -> None:
        assert compare_metric(1.0, 2.0, "minimize") is True

    def test_minimize_higher_is_worse(self) -> None:
        assert compare_metric(3.0, 2.0, "minimize") is False

    def test_minimize_equal_is_not_better(self) -> None:
        assert compare_metric(2.0, 2.0, "minimize") is False

    def test_invalid_direction_raises(self) -> None:
        with pytest.raises(ValueError, match="direction"):
            compare_metric(1.0, 2.0, "unknown")
