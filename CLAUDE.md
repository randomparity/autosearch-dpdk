# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
uv sync --group dev          # install deps + dev tools
uv run pytest -q             # run all tests
uv run pytest tests/test_schema.py -q                 # run one test file
uv run pytest tests/test_schema.py::TestSerialization  # run one test class
uv run ruff check autoforge/ autoforge_dpdk/ tests/    # lint
uv run ruff format autoforge/ autoforge_dpdk/ tests/   # format
uv run autoforge context                               # show optimization state
uv run autoforge submit -d "description"               # submit change for testing
uv run autoforge poll                                  # wait for runner result
uv run autoforge judge                                 # keep or revert based on metric
uv run autoforge baseline                              # submit baseline (no changes)
uv run autoforge hints                                 # show arch optimization hints
uv run autoforge hints --list                           # list available hint topics
uv run autoforge hints --topic perf-counters            # show perf counter reference
uv run autoforge-loop --dry-run                         # interactive mode (manual fallback)
```

## Architecture

Plugin-based optimization framework. An **agent** (workstation) proposes source changes and a **runner** (lab machine) builds, deploys, and measures performance via a loaded plugin. They communicate via git — JSON request files in `requests/`, results pushed back.

### Plugin system

Plugins are discovered via Python entry points (`autoforge.plugins` group). Each plugin provides three components:
- **Builder** — compiles the project from source
- **Deployer** — deploys build artifacts to the test target (bare metal, container, QEMU)
- **Tester** — runs performance tests and returns metrics

The DPDK plugin (`autoforge_dpdk/`) is the reference implementation. External plugins (vLLM, kernel) are separate packages.

### Protocol flow

```
pending → claimed → building → running → completed
                                       → failed (from any state)
```

`TestRequest` dataclass in `autoforge/protocol/schema.py` is the shared contract. Both sides serialize it as JSON files named `{seq:04d}_{timestamp}.json`. Status transitions are enforced by `VALID_TRANSITIONS`.

### Package boundaries

```
autoforge/protocol/    Shared: TestRequest, status constants, StatusLiteral, extract_metric
autoforge/plugins/     Plugin protocols (Builder, Deployer, Tester, Plugin) and loader
autoforge/agent/       Workstation: CLI subcommands, git ops, history tracking
autoforge/runner/      Lab machine: service loop, git-based state transitions
autoforge/perf/        Profiling: perf record orchestration, stack analysis, arch profiles
autoforge_dpdk/        DPDK plugin: meson+ninja build, testpmd/DTS testing
```

**Import rules:** `agent/` and `runner/` both import from `protocol/` and `plugins/`, never from each other. `autoforge_dpdk/` imports from `autoforge.plugins.protocols` for result types and from `autoforge.perf` for profiling. Always import from `autoforge.protocol` (the facade), not `autoforge.protocol.schema` directly.

### Agent modules

- `cli.py` — CLI subcommands (`context`, `submit`, `poll`, `judge`, `baseline`, `revert`, `build-log`, `status`, `hints`) for Claude Code
- `hints.py` — architecture-specific optimization hints lookup (supports topics: optimization, perf-counters)
- `loop.py` — interactive iteration loop (manual fallback)
- `git_ops.py` — git subprocess wrappers (`GIT_TIMEOUT=60`), `record_result_or_revert()`, `full_revert()`, `force_push_source()`
- `campaign.py` — `CampaignConfig` TypedDict, `load_campaign()`
- `strategy.py` — `format_context()`, `validate_change()`, `extract_profile_summary()`
- `history.py` — TSV-based results/failures tracking
- `metric.py` — `compare_metric()`, `below_threshold()`, `Direction` Literal type
- `protocol.py` — request creation (`create_request()`), sequence numbering, `poll_for_completion()`, `find_request_by_seq()`

### Runner modules

- `service.py` — main polling loop, loads plugin, `execute_request()` orchestrates build→deploy→test→push
- `protocol.py` — git commit/push with retry, `claim()`, `update_status()`, `fail()`

### DPDK plugin modules (`autoforge_dpdk/`)

- `builder.py` — `DpdkBuilder`: meson + ninja build orchestration
- `deployer.py` — `DpdkDeployer`: trivial pass-through (bare-metal builds)
- `tester.py` — `DpdkTester`: PTY-based testpmd execution, DTS test suites, profiling integration

### Configuration

- `config/campaign.toml` — what to optimize (metric, goal, project scope, test backend, plugin selection)
- `config/runner.toml` — where to build/test (paths, PCI addresses, lcores, timeouts). Gitignored; copy from `runner.toml.example`
- `pyelftools` in dependencies is required by DPDK's meson build, not by this project's Python code

### Key types

- `StatusLiteral` — `Literal["pending", "claimed", "building", "running", "completed", "failed"]`
- `CampaignConfig` — TypedDict hierarchy matching campaign TOML structure
- `ProjectConfig` — plugin name + project-specific config (scope, submodule_path, etc.)
- `Direction` — `Literal["maximize", "minimize"]`
- Plugin protocols: `Builder`, `Deployer`, `Tester`, `Plugin` in `autoforge.plugins.protocols`
- Plugin results: `BuildResult`, `DeployResult`, `TestResult` in `autoforge.plugins.protocols`

## Agent mode (Claude Code)

Read `program.md` for autonomous optimization instructions. The agent uses CLI subcommands (`uv run autoforge context/submit/poll/judge`) to interact with the remote runner. Start with: "read program.md and start experimenting".

## Sprint organization

All experiment artifacts are organized per-sprint under `sprints/<name>/`:
```
sprints/2026-03-23-memif-ppc64le/
  campaign.toml     # frozen snapshot of campaign config
  requests/         # request JSON files
  results.tsv       # iteration history
  failures.tsv      # failed optimization attempts (created on first failure)
  docs/             # summary, graphs
```

The active sprint is set in `config/campaign.toml` under `[sprint] name = "..."`. Use `uv run autoforge sprint init <name>` to create a new sprint. Sprint names must match `YYYY-MM-DD-slug` format.

Never commit test or scratch request files — they will pollute the real results history. Tests should use `tmp_path` fixtures, not sprint directories.

## Pull requests

Before creating a PR, run the `markdown-doc-reviewer` agent on the branch to verify that documentation (README, CLAUDE.md, docstrings, inline comments) is consistent with code changes. Fix any findings before opening the PR.

## Style

- Python 3.13, `from __future__ import annotations` in every file
- 100-char line length, ruff for lint+format
- All subprocess calls must include `timeout=` parameter
- Use `autoforge.protocol` facade for imports, not `autoforge.protocol.schema` directly
- `pyelftools` must stay in dependencies — do not remove it
