"""Tests for plugin discovery and loading."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from autoforge.plugins import (
    Builder,
    BuildResult,
    Deployer,
    DeployResult,
    Plugin,
    Tester,
    TestResult,
    list_plugins,
    load_plugin,
)


class FakeBuilder:
    def configure(self, project_config, runner_config):
        pass

    def build(self, source_path, commit, build_dir, timeout):
        return BuildResult(success=True, log="ok", duration_seconds=1.0)


class FakeDeployer:
    def configure(self, project_config, runner_config):
        pass

    def deploy(self, build_result):
        return DeployResult(success=True)


class FakeTester:
    def configure(self, project_config, runner_config):
        pass

    def test(self, deploy_result, timeout):
        return TestResult(
            success=True,
            metric_value=1.0,
            results_json=None,
            results_summary=None,
            error=None,
            duration_seconds=1.0,
        )


class FakePlugin:
    name = "fake"

    def create_builder(self):
        return FakeBuilder()

    def create_deployer(self):
        return FakeDeployer()

    def create_tester(self):
        return FakeTester()


class TestProtocolConformance:
    def test_fake_plugin_satisfies_protocol(self) -> None:
        plugin = FakePlugin()
        assert isinstance(plugin, Plugin)
        assert isinstance(plugin.create_builder(), Builder)
        assert isinstance(plugin.create_deployer(), Deployer)
        assert isinstance(plugin.create_tester(), Tester)


class TestLoadPlugin:
    def test_loads_registered_plugin(self) -> None:
        mock_ep = MagicMock()
        mock_ep.name = "fake"
        mock_ep.load.return_value = FakePlugin

        with patch("autoforge.plugins.loader.entry_points", return_value=[mock_ep]):
            plugin = load_plugin("fake")

        assert plugin.name == "fake"
        mock_ep.load.assert_called_once()

    def test_raises_on_missing_plugin(self) -> None:
        with (
            patch("autoforge.plugins.loader.entry_points", return_value=[]),
            pytest.raises(ValueError, match="not found"),
        ):
            load_plugin("nonexistent")

    def test_error_lists_installed_plugins(self) -> None:
        mock_ep = MagicMock()
        mock_ep.name = "dpdk"

        with (
            patch("autoforge.plugins.loader.entry_points", return_value=[mock_ep]),
            pytest.raises(ValueError, match="dpdk"),
        ):
            load_plugin("missing")


class TestListPlugins:
    def test_returns_plugin_names(self) -> None:
        ep1 = MagicMock()
        ep1.name = "dpdk"
        ep2 = MagicMock()
        ep2.name = "vllm"

        with patch("autoforge.plugins.loader.entry_points", return_value=[ep1, ep2]):
            names = list_plugins()

        assert names == ["dpdk", "vllm"]

    def test_empty_when_no_plugins(self) -> None:
        with patch("autoforge.plugins.loader.entry_points", return_value=[]):
            assert list_plugins() == []


class TestResultDataclasses:
    def test_build_result_defaults(self) -> None:
        r = BuildResult(success=True, log="ok", duration_seconds=1.0)
        assert r.artifacts == {}

    def test_deploy_result_defaults(self) -> None:
        r = DeployResult(success=True)
        assert r.error is None
        assert r.target_info == {}

    def test_test_result_fields(self) -> None:
        r = TestResult(
            success=False,
            metric_value=None,
            results_json={"key": "val"},
            results_summary="summary",
            error="failed",
            duration_seconds=5.0,
        )
        assert not r.success
        assert r.error == "failed"
