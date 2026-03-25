# Autosearch DPDK

Iterative DPDK performance optimization through automated build-test cycles on
real hardware.

## How it works

An **agent** on your workstation proposes source changes to DPDK and writes a
test request JSON file. It commits the request and the DPDK submodule pointer,
then pushes. A **runner** on a lab machine polls git, claims the request, builds
DPDK at the specified commit, runs performance tests (testpmd or DTS), and
pushes the results back. The agent detects completion, records the metric,
keeps good changes, reverts bad ones, and starts the next iteration.

```
Agent                              Runner
  |  write request JSON              |
  |  commit + push ----------------> |
  |                         claim request
  |                         build DPDK
  |                         run testpmd / DTS
  |  <-------------- push results    |
  |  read metric, keep or revert     |
```

## Quick start

Prerequisites: Python 3.13+, [uv](https://docs.astral.sh/uv/), git.

```bash
git clone --recurse-submodules <repo-url>
cd autosearch-dpdk
uv sync
uv run autosearch context            # show current optimization state
uv run autosearch-loop --dry-run     # interactive mode (no git push)
```

The agent is Claude Code. Each sprint has a `program.md` with its optimization goals — read the active sprint's program file for the full autonomous workflow.

For full setup, see the [agent guide](docs/agent.md) and
[runner guide](docs/runner.md).

## Project layout

```
src/
  agent/       CLI subcommands, git ops, history, metric, sprint management
  runner/      Runner service, build, testpmd, execute (DTS), protocol
  protocol/    Shared schema (TestRequest dataclass)
  perf/        Profiling: perf record, stack analysis, arch profiles
  logging_config.py  Shared logging setup
config/
  campaign.toml           Campaign settings (metric, goal, test backend, scope)
  runner.toml.example     Runner-specific paths, build, and testpmd settings
sprints/       Per-sprint experiment artifacts
  <name>/
    requests/       Test request JSON files
    results.tsv     Iteration history
    failures.tsv    Reverted attempts (created on first failure)
    campaign.toml   Frozen campaign config snapshot
    docs/           Summaries and graphs
dpdk/          DPDK source (git submodule)
docs/          Documentation
```

## Development

```bash
uv sync --group dev
uv run pytest -q
uv run ruff check src/ tests/
uv run ruff format src/ tests/
```

## Documentation

- [Agent guide](docs/agent.md) — workstation setup, campaign config, running
- [Runner guide](docs/runner.md) — lab machine setup, testpmd/DTS config, deployment
