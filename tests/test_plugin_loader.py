"""Tests for file-based plugin discovery and loading."""

from __future__ import annotations

import pytest

from autoforge.plugins import (
    Builder,
    BuildResult,
    Deployer,
    DeployResult,
    Profiler,
    ProfileResult,
    Tester,
    TestResult,
)
from autoforge.plugins.loader import (
    PipelineComponents,
    list_components,
    load_component,
    load_pipeline,
)

BUILDER_SOURCE = """\
from autoforge.plugins.protocols import BuildResult

class LocalServerBuilder:
    name = "local-server"

    def configure(self, project_config, runner_config):
        pass

    def build(self, source_path, commit, build_dir, timeout):
        return BuildResult(success=True, log="ok", duration_seconds=1.0)
"""

DEPLOYER_SOURCE = """\
from autoforge.plugins.protocols import DeployResult

class LocalDeployer:
    name = "local"

    def configure(self, project_config, runner_config):
        pass

    def deploy(self, build_result):
        return DeployResult(success=True)
"""

TESTER_SOURCE = """\
from autoforge.plugins.protocols import TestResult

class MemifTester:
    name = "testpmd-memif"

    def configure(self, project_config, runner_config):
        pass

    def test(self, deploy_result, timeout):
        return TestResult(
            success=True, metric_value=86.0,
            results_json=None, results_summary=None,
            error=None, duration_seconds=10.0,
        )
"""

PROFILER_SOURCE = """\
from autoforge.plugins.protocols import ProfileResult

class PerfRecordProfiler:
    name = "perf-record"

    def configure(self, project_config, runner_config):
        pass

    def profile(self, pid, duration, config):
        return ProfileResult(success=True, summary={"top": []})
"""


def _setup_project(tmp_path, project="testproj"):
    """Create a project with one plugin per category."""
    base = tmp_path / project
    (base / "builds").mkdir(parents=True)
    (base / "deploys").mkdir(parents=True)
    (base / "tests").mkdir(parents=True)
    (base / "perfs").mkdir(parents=True)

    (base / "builds" / "local-server.py").write_text(BUILDER_SOURCE)
    (base / "deploys" / "local.py").write_text(DEPLOYER_SOURCE)
    (base / "tests" / "testpmd-memif.py").write_text(TESTER_SOURCE)
    (base / "perfs" / "perf-record.py").write_text(PROFILER_SOURCE)

    return base


class TestLoadComponent:
    def test_loads_builder(self, tmp_path) -> None:
        _setup_project(tmp_path)
        comp = load_component("testproj", "build", "local-server", root=tmp_path)
        assert isinstance(comp, Builder)
        assert comp.name == "local-server"

    def test_loads_deployer(self, tmp_path) -> None:
        _setup_project(tmp_path)
        comp = load_component("testproj", "deploy", "local", root=tmp_path)
        assert isinstance(comp, Deployer)
        assert comp.name == "local"

    def test_loads_tester(self, tmp_path) -> None:
        _setup_project(tmp_path)
        comp = load_component("testproj", "test", "testpmd-memif", root=tmp_path)
        assert isinstance(comp, Tester)
        assert comp.name == "testpmd-memif"

    def test_loads_profiler(self, tmp_path) -> None:
        _setup_project(tmp_path)
        comp = load_component("testproj", "profiler", "perf-record", root=tmp_path)
        assert isinstance(comp, Profiler)
        assert comp.name == "perf-record"

    def test_missing_plugin_raises(self, tmp_path) -> None:
        _setup_project(tmp_path)
        with pytest.raises(FileNotFoundError, match="not found"):
            load_component("testproj", "build", "nonexistent", root=tmp_path)

    def test_invalid_category_raises(self, tmp_path) -> None:
        with pytest.raises(ValueError, match="Invalid category"):
            load_component("testproj", "bogus", "anything", root=tmp_path)

    def test_no_conforming_class_raises(self, tmp_path) -> None:
        base = tmp_path / "testproj" / "builds"
        base.mkdir(parents=True)
        (base / "bad.py").write_text("class NotABuilder:\n    pass\n")
        with pytest.raises(ValueError, match="No class conforming"):
            load_component("testproj", "build", "bad", root=tmp_path)


class TestListComponents:
    def test_lists_available(self, tmp_path) -> None:
        _setup_project(tmp_path)
        names = list_components("testproj", "build", root=tmp_path)
        assert names == ["local-server"]

    def test_empty_category(self, tmp_path) -> None:
        base = tmp_path / "testproj" / "builds"
        base.mkdir(parents=True)
        assert list_components("testproj", "build", root=tmp_path) == []

    def test_missing_dir_returns_empty(self, tmp_path) -> None:
        assert list_components("nonexistent", "build", root=tmp_path) == []

    def test_multiple_plugins(self, tmp_path) -> None:
        _setup_project(tmp_path)
        base = tmp_path / "testproj" / "builds"
        (base / "remote-server.py").write_text(
            BUILDER_SOURCE.replace("local-server", "remote-server").replace(
                "LocalServerBuilder", "RemoteServerBuilder"
            )
        )
        names = list_components("testproj", "build", root=tmp_path)
        assert names == ["local-server", "remote-server"]


class TestLoadPipeline:
    def test_loads_full_pipeline(self, tmp_path) -> None:
        _setup_project(tmp_path)
        campaign = {
            "project": {
                "build": "local-server",
                "deploy": "local",
                "test": "testpmd-memif",
                "profiler": "perf-record",
            }
        }
        pipeline = load_pipeline("testproj", campaign, root=tmp_path)
        assert isinstance(pipeline, PipelineComponents)
        assert isinstance(pipeline.builder, Builder)
        assert isinstance(pipeline.deployer, Deployer)
        assert isinstance(pipeline.tester, Tester)
        assert isinstance(pipeline.profiler, Profiler)

    def test_profiler_optional(self, tmp_path) -> None:
        _setup_project(tmp_path)
        campaign = {
            "project": {
                "build": "local-server",
                "deploy": "local",
                "test": "testpmd-memif",
            }
        }
        pipeline = load_pipeline("testproj", campaign, root=tmp_path)
        assert pipeline.profiler is None

    def test_missing_build_raises(self, tmp_path) -> None:
        campaign = {"project": {"deploy": "local", "test": "testpmd-memif"}}
        with pytest.raises(ValueError, match="build"):
            load_pipeline("testproj", campaign, root=tmp_path)

    def test_missing_deploy_raises(self, tmp_path) -> None:
        campaign = {"project": {"build": "local-server", "test": "testpmd-memif"}}
        with pytest.raises(ValueError, match="deploy"):
            load_pipeline("testproj", campaign, root=tmp_path)

    def test_missing_test_raises(self, tmp_path) -> None:
        campaign = {"project": {"build": "local-server", "deploy": "local"}}
        with pytest.raises(ValueError, match="test"):
            load_pipeline("testproj", campaign, root=tmp_path)


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

    def test_profile_result_defaults(self) -> None:
        r = ProfileResult(success=True)
        assert r.summary == {}
        assert r.error is None
        assert r.duration_seconds == 0.0
