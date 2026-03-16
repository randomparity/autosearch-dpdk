# Autosearch DPDK

Iterative DPDK performance optimization through automated build-test cycles on
real hardware.

## How it works

An **agent** on your workstation proposes source changes to DPDK and writes a
test request JSON file. It commits the request and the DPDK submodule pointer,
then pushes. A **runner** on a lab machine polls git, claims the request, builds
DPDK at the specified commit, executes DTS performance tests, and pushes the
results back. The agent detects completion, records the metric, and starts the
next iteration.

```
Agent                              Runner
  |  write request JSON              |
  |  commit + push ----------------> |
  |                         claim request
  |                         build DPDK
  |                         run DTS tests
  |  <-------------- push results    |
  |  read metric, record in TSV      |
```

## Quick start

Prerequisites: Python 3.13+, [uv](https://docs.astral.sh/uv/), git.

```bash
git clone --recurse-submodules <repo-url>
cd autosearch-dpdk
uv sync
uv run autosearch --dry-run          # local test (no git push)
```

For full setup, see the [agent guide](docs/agent.md) and
[runner guide](docs/runner.md).

## Project layout

```
src/
  agent/       Agent loop, protocol, history, metric, strategy
  runner/      Runner service, build, execute, protocol
  protocol/    Shared schema (TestRequest dataclass)
config/
  campaign.toml           Campaign settings (metric, DTS suites, scope)
  runner.toml.example     Runner-specific paths and timeouts
  nodes.yaml.example      DTS node definitions (two-node topology)
  test_run.yaml.example   DTS test run configuration
requests/      Test request JSON files (the communication protocol)
results.tsv    Cumulative iteration history
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
- [Runner guide](docs/runner.md) — lab machine setup, DTS config, deployment
