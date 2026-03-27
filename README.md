# Autoforge

Iterative performance optimization through automated build-test cycles on
real hardware.

## How it works

An **agent** on your workstation proposes source changes to a specified source
code repository cloned on your system and writes a test request JSON file. It
commits the request and the repo submodule pointer, then pushes. A **runner**
on a lab machine polls git, claims the request, builds the source code at the
specified commit, runs performance tests, and pushes the results back. The
agent detects completion, records the metric, keeps good changes, reverts bad
ones, and starts the next iteration.

```
Agent                              Runner
  |  write request JSON              |
  |  commit + push ----------------> |
  |                         claim request
  |                         build source code
  |                         run desired tests
  |  <-------------- push results    |
  |  read metric, keep or revert     |
```

## Quick start

Prerequisites: Python 3.13+, [uv](https://docs.astral.sh/uv/), git.

```bash
git clone --recurse-submodules <repo-url> autoforge
cd autoforge
uv sync
uv run autoforge context             # show current optimization state
uv run autoforge-loop --dry-run      # interactive mode (no git push)
```

The agent is Claude Code. Each sprint has a `program.md` with its optimization goals — read the active sprint's program file for the full autonomous workflow.

For full setup, see the [agent guide](docs/agent.md) and
[runner guide](docs/runner.md).

## Project layout

```
autoforge/
  agent/       CLI subcommands, git ops, history, metric, sprint management
  runner/      Runner service, phase runners, protocol
  protocol/    Shared schema (TestRequest dataclass)
  plugins/     Plugin protocols (Builder, Deployer, Tester, Profiler) and loader
  perf/        Profiling: perf record, stack analysis, arch profiles
  pointer.py   Pointer file (.autoforge.toml) load/save
  campaign.py  Campaign config accessor functions and resolution
  config.py    Config loading with local overrides and ${VAR} resolution
projects/
  <project>/
    builds/          Build plugins + shared .toml configs
    deploys/         Deploy plugins + shared .toml configs
    tests/           Test plugins + shared .toml configs
    perfs/           Profiler plugins + shared .toml configs
    judges/          Judge plugins (optional)
    runner.toml      Shared runner config (tracked in git)
    sprints/
      <name>/
        campaign.toml   Campaign config for this sprint
        program.md      Optimization goals for this sprint
        requests/       Test request JSON files
        results.tsv     Iteration history
        docs/           Summaries and graphs
docs/            Documentation
```

## Projects

- [DPDK](projects/dpdk/) — packet processing throughput (testpmd, DTS)
- [vLLM](projects/vllm/) — LLM inference serving throughput (containerized, GPU)

## Development

```bash
uv sync --group dev
uv run pytest -q
uv run ruff check autoforge/ tests/
uv run ruff format autoforge/ tests/
```

## Documentation

- [Agent guide](docs/agent.md) — workstation setup, campaign config, running
- [Runner guide](docs/runner.md) — lab machine setup, testpmd/DTS config, deployment
- [Plugin SDK](docs/plugin-sdk.md) — authoring guide for build, deploy, test, and profiler plugins
