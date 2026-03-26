"""Tests for autoforge doctor configuration validation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from autoforge.agent.doctor import (
    check_campaign,
    check_plugins,
    check_pointer,
    check_runner,
    check_sprint,
    run_doctor,
)


def _write_toml(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def _build_config_tree(
    root: Path,
    *,
    pointer: bool = True,
    campaign: bool = True,
    runner: bool = True,
    plugins: bool = True,
    sprint_structure: bool = True,
    campaign_overrides: dict[str, Any] | None = None,
    runner_overrides: str | None = None,
) -> tuple[str, str]:
    """Build a valid config tree under root. Returns (project, sprint)."""
    project = "testproj"
    sprint = "2026-01-01-test"

    project_dir = root / "projects" / project
    sprint_dir = project_dir / "sprints" / sprint

    if pointer:
        _write_toml(
            root / ".autoforge.toml",
            f'project = "{project}"\nsprint = "{sprint}"\n',
        )

    if sprint_structure:
        (sprint_dir / "requests").mkdir(parents=True, exist_ok=True)
        (sprint_dir / "results.tsv").write_text("seq\tmetric\n")

    if campaign:
        _write_toml(
            sprint_dir / "campaign.toml",
            '[campaign]\nname = "test"\n'
            "[metric]\n"
            'name = "throughput"\npath = "throughput"\n'
            'direction = "maximize"\nthreshold = 0.01\n'
            "[project]\n"
            f'name = "{project}"\n'
            'build = "local"\ndeploy = "local"\n'
            'test = "testpmd-memif"\n'
            f'submodule_path = "projects/{project}/repo"\n'
            'scope = ["src/"]\n',
        )
        # Create the submodule path
        (root / "projects" / project / "repo").mkdir(
            parents=True,
            exist_ok=True,
        )

    if runner:
        content = runner_overrides or (
            '[runner]\nphase = "all"\n'
            "[paths]\n"
            f'dpdk_src = "{root / "fakesrc"}"\n'
            f'build_dir = "{root / "build"}"\n'
            "[timeouts]\nbuild_minutes = 30\ntest_minutes = 10\n"
        )
        _write_toml(project_dir / "runner.toml", content)

    if plugins:
        builds_dir = project_dir / "builds"
        deploys_dir = project_dir / "deploys"
        tests_dir = project_dir / "tests"

        builds_dir.mkdir(parents=True, exist_ok=True)
        (builds_dir / "local.py").write_text("# plugin\n")
        _write_toml(builds_dir / "local.toml", "[build]\njobs = 0\n")

        deploys_dir.mkdir(parents=True, exist_ok=True)
        (deploys_dir / "local.py").write_text("# plugin\n")

        tests_dir.mkdir(parents=True, exist_ok=True)
        (tests_dir / "testpmd-memif.py").write_text("# plugin\n")
        _write_toml(
            tests_dir / "testpmd-memif.toml",
            '[testpmd]\nlcores = "4-7"\n',
        )

    return project, sprint


class TestCheckPointer:
    def test_missing_pointer(self, tmp_path: Path) -> None:
        results = check_pointer(tmp_path)
        assert len(results) == 1
        assert results[0].status == "fail"
        assert results[0].name == "pointer.file_exists"

    def test_invalid_toml(self, tmp_path: Path) -> None:
        (tmp_path / ".autoforge.toml").write_text("not valid [[ toml")
        results = check_pointer(tmp_path)
        assert any(r.name == "pointer.valid_toml" and r.status == "fail" for r in results)

    def test_missing_project_dir(self, tmp_path: Path) -> None:
        _write_toml(
            tmp_path / ".autoforge.toml",
            'project = "nope"\nsprint = "2026-01-01-test"\n',
        )
        results = check_pointer(tmp_path)
        assert any(r.name == "pointer.project_exists" and r.status == "fail" for r in results)

    def test_valid_pointer(self, tmp_path: Path) -> None:
        _build_config_tree(tmp_path, campaign=False, runner=False, plugins=False)
        results = check_pointer(tmp_path)
        assert all(r.status == "pass" for r in results)


class TestCheckCampaign:
    def test_missing_campaign(self, tmp_path: Path) -> None:
        project, sprint = _build_config_tree(
            tmp_path,
            campaign=False,
            runner=False,
            plugins=False,
        )
        results = check_campaign(project, sprint, tmp_path)
        assert results[0].status == "fail"
        assert results[0].name == "campaign.file_exists"

    def test_bad_direction(self, tmp_path: Path) -> None:
        project, sprint = _build_config_tree(tmp_path, runner=False, plugins=False)
        # Overwrite campaign with bad direction
        path = tmp_path / "projects" / project / "sprints" / sprint / "campaign.toml"
        _write_toml(
            path,
            '[campaign]\nname = "test"\n'
            '[metric]\nname = "x"\npath = "x"\n'
            'direction = "up"\nthreshold = 0.01\n'
            '[project]\nname = "testproj"\n'
            'build = "local"\ntest = "testpmd-memif"\n'
            'scope = ["src/"]\n',
        )
        results = check_campaign(project, sprint, tmp_path)
        assert any(r.name == "campaign.metric_direction" and r.status == "fail" for r in results)

    def test_profiling_without_profiler(self, tmp_path: Path) -> None:
        project, sprint = _build_config_tree(tmp_path, runner=False, plugins=False)
        path = tmp_path / "projects" / project / "sprints" / sprint / "campaign.toml"
        _write_toml(
            path,
            '[campaign]\nname = "test"\n'
            '[metric]\nname = "x"\npath = "x"\n'
            'direction = "maximize"\nthreshold = 0.01\n'
            "[profiling]\nenabled = true\n"
            '[project]\nname = "testproj"\n'
            'build = "local"\ntest = "testpmd-memif"\n'
            'scope = ["src/"]\n',
        )
        results = check_campaign(project, sprint, tmp_path)
        assert any(r.name == "campaign.profiler_set" and r.status == "fail" for r in results)


class TestCheckRunner:
    def test_runner_skipped_for_agent_role(self, tmp_path: Path) -> None:
        results = check_runner("testproj", "agent", tmp_path)
        assert len(results) == 1
        assert results[0].status == "pass"
        assert "skipped" in results[0].message

    def test_missing_runner_toml(self, tmp_path: Path) -> None:
        project, _ = _build_config_tree(
            tmp_path,
            runner=False,
            plugins=False,
            campaign=False,
        )
        results = check_runner(project, "runner", tmp_path)
        assert results[0].status == "fail"
        assert "runner.toml not found" in results[0].message

    def test_invalid_runner_phase(self, tmp_path: Path) -> None:
        project, _ = _build_config_tree(
            tmp_path,
            campaign=False,
            plugins=False,
            runner_overrides=(
                '[runner]\nphase = "bogus"\n'
                "[paths]\n"
                'dpdk_src = "/tmp/fake"\nbuild_dir = "/tmp/build"\n'
                "[timeouts]\nbuild_minutes = 30\ntest_minutes = 10\n"
            ),
        )
        results = check_runner(project, "all", tmp_path)
        assert any(r.name == "runner.phase" and r.status == "fail" for r in results)


class TestCheckPlugins:
    def test_missing_plugin_file(self, tmp_path: Path) -> None:
        project, _ = _build_config_tree(
            tmp_path,
            plugins=False,
            runner=False,
        )
        campaign_data = {
            "project": {"build": "local", "test": "testpmd-memif"},
        }
        results = check_plugins(project, campaign_data, tmp_path)
        assert any(r.name == "plugin.build.file_exists" and r.status == "fail" for r in results)

    def test_missing_plugin_toml_is_warning(self, tmp_path: Path) -> None:
        project, _ = _build_config_tree(tmp_path, runner=False)
        # Remove the build plugin toml
        toml_path = tmp_path / "projects" / project / "builds" / "local.toml"
        toml_path.unlink()
        campaign_data = {
            "project": {
                "build": "local",
                "deploy": "local",
                "test": "testpmd-memif",
            },
        }
        results = check_plugins(project, campaign_data, tmp_path)
        assert any(r.name == "plugin.build.config_exists" and r.status == "warn" for r in results)


class TestCheckSprint:
    def test_missing_requests_dir(self, tmp_path: Path) -> None:
        project, sprint = _build_config_tree(
            tmp_path,
            sprint_structure=False,
            runner=False,
            plugins=False,
        )
        # Create sprint dir but not requests/
        sprint_dir = tmp_path / "projects" / project / "sprints" / sprint
        sprint_dir.mkdir(parents=True, exist_ok=True)
        results = check_sprint(project, sprint, tmp_path)
        assert any(r.name == "sprint.requests_dir" and r.status == "fail" for r in results)


class TestRunDoctor:
    def test_healthy_setup(self, tmp_path: Path) -> None:
        _build_config_tree(tmp_path)
        # Create the dpdk_src path so runner check passes
        (tmp_path / "fakesrc").mkdir()
        results = run_doctor(role="all", root=tmp_path)
        fails = [r for r in results if r.status == "fail"]
        assert fails == [], f"Unexpected failures: {fails}"

    def test_missing_pointer_early_exit(self, tmp_path: Path) -> None:
        results = run_doctor(role="all", root=tmp_path)
        assert len(results) == 1
        assert results[0].status == "fail"
        assert results[0].layer == "pointer"
