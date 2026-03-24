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
uv run autosearch --dry-run          # local test (no git push)
```

### Autonomous mode

The agent can use Claude to propose DPDK changes automatically. Set your
API key and run with `--autonomous`:

```bash
# Using Anthropic directly
export ANTHROPIC_API_KEY=sk-...
uv run autosearch --autonomous --dry-run

# Using OpenRouter
export OPENROUTER_API_KEY=sk-or-...
uv run autosearch --autonomous --provider openrouter --dry-run
```

| Flag | Description |
|------|-------------|
| `--dry-run` | Skip git push (local testing only) |
| `--autonomous` | Use Claude API for automated change proposals |
| `--provider` | API provider: `anthropic` (default) or `openrouter` |
| `--campaign <path>` | Path to campaign TOML config (default: `config/campaign.toml`) |
| `--log-level` | Log level: `debug`, `info`, `warning`, `error` (default: info) |
| `--log-file <path>` | Also write logs to a file |

For full setup, see the [agent guide](docs/agent.md) and
[runner guide](docs/runner.md).

## Project layout

```
src/
  agent/       Agent loop, protocol, history, metric, strategy
  runner/      Runner service, build, testpmd, execute (DTS), protocol
  protocol/    Shared schema (TestRequest dataclass)
  perf/        Profiling: perf record, stack analysis, arch profiles
  logging_config.py  Shared logging setup
config/
  campaign.toml           Campaign settings (metric, goal, test backend, scope)
  runner.toml.example     Runner-specific paths, build, and testpmd settings
requests/      Test request JSON files (the communication protocol)
results.tsv    Cumulative iteration history
failures.tsv   Record of reverted optimization attempts
dpdk/          DPDK v25.11 source (git submodule)
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
