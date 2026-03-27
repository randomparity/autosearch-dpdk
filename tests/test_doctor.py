"""Tests for autoforge doctor configuration validation."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

from autoforge.agent.doctor import (
    _check_sensitive_empty,
    check_campaign,
    check_optimization_branch,
    check_plugins,
    check_pointer,
    check_runner,
    check_sprint,
    format_effective_config,
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
    git_submodule: bool = True,
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
            f'optimization_branch = "autoforge/2026-01-01-test"\n'
            'scope = ["src/"]\n',
        )
        # Create the submodule path
        repo_dir = root / "projects" / project / "repo"
        repo_dir.mkdir(parents=True, exist_ok=True)
        if git_submodule:
            # Simulate git repo with .git marker
            (repo_dir / ".git").write_text("")

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
        assert results[0].path == ".autoforge.toml"

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

    def test_results_include_file_paths(self, tmp_path: Path) -> None:
        _build_config_tree(tmp_path, campaign=False, runner=False, plugins=False)
        results = check_pointer(tmp_path)
        file_check = next(r for r in results if r.name == "pointer.file_exists")
        assert file_check.path == ".autoforge.toml"


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
        assert "campaign.toml" in results[0].path

    def test_bad_direction(self, tmp_path: Path) -> None:
        project, sprint = _build_config_tree(tmp_path, runner=False, plugins=False)
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

    def test_missing_deploy_field(self, tmp_path: Path) -> None:
        project, sprint = _build_config_tree(tmp_path, runner=False, plugins=False)
        path = tmp_path / "projects" / project / "sprints" / sprint / "campaign.toml"
        _write_toml(
            path,
            '[campaign]\nname = "test"\n'
            '[metric]\nname = "x"\npath = "x"\n'
            'direction = "maximize"\nthreshold = 0.01\n'
            '[project]\nname = "testproj"\n'
            'build = "local"\ntest = "testpmd-memif"\n'
            'scope = ["src/"]\n',
        )
        results = check_campaign(project, sprint, tmp_path)
        assert any(r.name == "campaign.project_deploy" and r.status == "fail" for r in results)

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

    def test_submodule_not_git_repo(self, tmp_path: Path) -> None:
        project, sprint = _build_config_tree(
            tmp_path,
            runner=False,
            plugins=False,
            git_submodule=False,
        )
        results = check_campaign(project, sprint, tmp_path)
        sub_check = next(r for r in results if r.name == "campaign.submodule_path")
        assert sub_check.status == "warn"
        assert "not a git repository" in sub_check.message

    def test_submodule_is_git_repo(self, tmp_path: Path) -> None:
        project, sprint = _build_config_tree(
            tmp_path,
            runner=False,
            plugins=False,
            git_submodule=True,
        )
        results = check_campaign(project, sprint, tmp_path)
        sub_check = next(r for r in results if r.name == "campaign.submodule_path")
        assert sub_check.status == "pass"
        assert "git repository" in sub_check.message


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
        assert "runner.toml" in results[0].path

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
        build_check = next(r for r in results if r.name == "plugin.build.file_exists")
        assert build_check.status == "fail"
        assert "builds/local.py" in build_check.path

    def test_missing_plugin_toml_is_warning(self, tmp_path: Path) -> None:
        project, _ = _build_config_tree(tmp_path, runner=False)
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

    def test_local_deploy_no_config_is_pass(self, tmp_path: Path) -> None:
        project, _ = _build_config_tree(tmp_path, runner=False)
        campaign_data = {
            "project": {
                "build": "local",
                "deploy": "local",
                "test": "testpmd-memif",
            },
        }
        results = check_plugins(project, campaign_data, tmp_path)
        deploy_config = next(r for r in results if r.name == "plugin.deploy.config_exists")
        assert deploy_config.status == "pass"
        assert "pass-through" in deploy_config.message

    def test_unexpected_section_in_local_override_warns(self, tmp_path: Path) -> None:
        project, _ = _build_config_tree(tmp_path, runner=False)
        tests_dir = tmp_path / "projects" / project / "tests"
        # Base config has only [testpmd]
        _write_toml(tests_dir / "testpmd-memif.toml", "[testpmd]\n")
        # Local override has [testpmd] + stray [profiling]
        _write_toml(
            tests_dir / "testpmd-memif.local.toml",
            '[testpmd]\nlcores = "4-7"\n[profiling]\nenabled = false\n',
        )
        campaign_data = {
            "project": {
                "build": "local",
                "deploy": "local",
                "test": "testpmd-memif",
            },
        }
        results = check_plugins(project, campaign_data, tmp_path)
        section_warns = [
            r for r in results if r.name == "plugin.test.config_sections" and r.status == "warn"
        ]
        assert len(section_warns) == 1
        assert "profiling" in section_warns[0].message

    def test_no_warning_when_local_sections_match_base(self, tmp_path: Path) -> None:
        project, _ = _build_config_tree(tmp_path, runner=False)
        tests_dir = tmp_path / "projects" / project / "tests"
        _write_toml(tests_dir / "testpmd-memif.toml", "[testpmd]\n")
        _write_toml(tests_dir / "testpmd-memif.local.toml", '[testpmd]\nlcores = "4-7"\n')
        campaign_data = {
            "project": {
                "build": "local",
                "deploy": "local",
                "test": "testpmd-memif",
            },
        }
        results = check_plugins(project, campaign_data, tmp_path)
        section_warns = [r for r in results if "config_sections" in r.name]
        assert section_warns == []


class TestCheckSprint:
    def test_missing_requests_dir(self, tmp_path: Path) -> None:
        project, sprint = _build_config_tree(
            tmp_path,
            sprint_structure=False,
            runner=False,
            plugins=False,
        )
        sprint_dir = tmp_path / "projects" / project / "sprints" / sprint
        sprint_dir.mkdir(parents=True, exist_ok=True)
        results = check_sprint(project, sprint, tmp_path)
        req_check = next(r for r in results if r.name == "sprint.requests_dir")
        assert req_check.status == "fail"
        assert "requests" in req_check.path


class TestRunDoctor:
    def test_healthy_setup(self, tmp_path: Path) -> None:
        _build_config_tree(tmp_path)
        (tmp_path / "fakesrc").mkdir()
        results, effective = run_doctor(role="all", root=tmp_path)
        fails = [r for r in results if r.status == "fail"]
        assert fails == [], f"Unexpected failures: {fails}"
        assert effective["project"] == "testproj"
        assert effective["sprint"] == "2026-01-01-test"

    def test_missing_pointer_early_exit(self, tmp_path: Path) -> None:
        results, effective = run_doctor(role="all", root=tmp_path)
        assert len(results) == 1
        assert results[0].status == "fail"
        assert results[0].layer == "pointer"
        assert effective == {}

    def test_effective_config_has_plugins(self, tmp_path: Path) -> None:
        _build_config_tree(tmp_path)
        (tmp_path / "fakesrc").mkdir()
        _, effective = run_doctor(role="all", root=tmp_path)
        assert effective["plugins"]["build"] == "local"
        assert effective["plugins"]["test"] == "testpmd-memif"

    def test_effective_config_format(self, tmp_path: Path) -> None:
        _build_config_tree(tmp_path)
        (tmp_path / "fakesrc").mkdir()
        _, effective = run_doctor(role="all", root=tmp_path)
        output = format_effective_config(effective)
        assert "project: testproj" in output
        assert "build: local" in output


class TestCheckOptimizationBranch:
    def test_missing_branch_is_fail(self, tmp_path: Path) -> None:
        results = check_optimization_branch("proj", "2026-01-01-test", {}, tmp_path)
        assert any(r.name == "campaign.optimization_branch" and r.status == "fail" for r in results)

    def test_canonical_branch_passes(self, tmp_path: Path) -> None:
        data = {"project": {"optimization_branch": "autoforge/2026-01-01-test"}}
        results = check_optimization_branch("proj", "2026-01-01-test", data, tmp_path)
        branch_check = next(r for r in results if r.name == "campaign.optimization_branch")
        assert branch_check.status == "pass"

    def test_noncanonical_branch_warns(self, tmp_path: Path) -> None:
        data = {"project": {"optimization_branch": "my/custom/branch"}}
        results = check_optimization_branch("proj", "2026-01-01-test", data, tmp_path)
        branch_check = next(r for r in results if r.name == "campaign.optimization_branch")
        assert branch_check.status == "warn"

    def test_branch_absent_from_submodule_warns(self, tmp_path: Path) -> None:
        sub = tmp_path / "projects" / "proj" / "repo"
        sub.mkdir(parents=True)
        (sub / ".git").write_text("")
        data = {
            "project": {
                "optimization_branch": "autoforge/2026-01-01-test",
                "submodule_path": "projects/proj/repo",
            }
        }
        with patch("autoforge.agent.doctor.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = ""  # branch not listed
            results = check_optimization_branch("proj", "2026-01-01-test", data, tmp_path)
        exists_check = next(
            (r for r in results if r.name == "campaign.optimization_branch_exists"), None
        )
        assert exists_check is not None
        assert exists_check.status == "warn"

    def test_branch_present_in_submodule_passes(self, tmp_path: Path) -> None:
        sub = tmp_path / "projects" / "proj" / "repo"
        sub.mkdir(parents=True)
        (sub / ".git").write_text("")
        data = {
            "project": {
                "optimization_branch": "autoforge/2026-01-01-test",
                "submodule_path": "projects/proj/repo",
            }
        }
        with patch("autoforge.agent.doctor.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = "  autoforge/2026-01-01-test\n"
            results = check_optimization_branch("proj", "2026-01-01-test", data, tmp_path)
        exists_check = next(
            (r for r in results if r.name == "campaign.optimization_branch_exists"), None
        )
        assert exists_check is not None
        assert exists_check.status == "pass"


class TestRedaction:
    def test_format_effective_config_redacts_sensitive_keys(self) -> None:
        config: dict[str, Any] = {
            "project": "myproj",
            "sprint": "2026-01-01-test",
            "plugin_configs": {
                "deploy/container-gpu.toml": {
                    "deploy": {
                        "env": {"HF_TOKEN": "hf_secret", "model": "Qwen/Qwen2-7B"},
                    }
                }
            },
        }
        output = format_effective_config(config)
        assert "hf_secret" not in output
        assert "<redacted>" in output
        assert "Qwen" in output

    def test_check_sensitive_empty_warns_on_blank(self) -> None:
        data: dict[str, Any] = {"deploy": {"env": {"HF_TOKEN": ""}}}
        results = _check_sensitive_empty(data, "deploys/container-gpu.toml", "deploy")
        assert len(results) == 1
        assert results[0].status == "warn"
        assert results[0].name == "plugin.deploy.config_empty_secret"
        assert "HF_TOKEN" in results[0].message

    def test_check_sensitive_empty_passes_when_filled(self) -> None:
        data: dict[str, Any] = {"deploy": {"env": {"HF_TOKEN": "hf_real"}}}
        results = _check_sensitive_empty(data, "deploys/container-gpu.toml", "deploy")
        assert results == []
