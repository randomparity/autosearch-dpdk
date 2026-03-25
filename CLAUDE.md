# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
uv sync --group dev          # install deps + dev tools
uv run pytest -q             # run all tests
uv run pytest tests/test_schema.py -q                 # run one test file
uv run pytest tests/test_schema.py::TestSerialization  # run one test class
uv run ruff check src/ tests/                          # lint
uv run ruff format src/ tests/                         # format
uv run autosearch context                              # show optimization state
uv run autosearch submit -d "description"              # submit change for testing
uv run autosearch poll                                 # wait for runner result
uv run autosearch judge                                # keep or revert based on metric
uv run autosearch baseline                             # submit baseline (no changes)
uv run autosearch hints                                 # show arch optimization hints
uv run autosearch hints --list                          # list available hint topics
uv run autosearch hints --topic perf-counters           # show perf counter reference
uv run autosearch-loop --dry-run                        # interactive mode (manual fallback)
```

## Architecture

Two-process system: an **agent** (workstation) proposes DPDK source changes and a **runner** (lab machine) builds and measures throughput. They communicate via git — JSON request files in `requests/`, results pushed back.

### Protocol flow

```
pending → claimed → building → running → completed
                                       → failed (from any state)
```

`TestRequest` dataclass in `src/protocol/schema.py` is the shared contract. Both sides serialize it as JSON files named `{seq:04d}_{timestamp}.json`. Status transitions are enforced by `VALID_TRANSITIONS`.

### Package boundaries

```
src/protocol/    Shared: TestRequest, status constants, StatusLiteral, extract_metric
src/agent/       Workstation: CLI subcommands, git ops, history tracking
src/runner/      Lab machine: build DPDK, run testpmd/DTS, push results
src/perf/        Profiling: perf record orchestration, stack analysis, arch profiles, diff comparison
```

**Import rules:** `agent/` and `runner/` both import from `protocol/`, never from each other. `runner/` imports from `perf/` for profiling captures; `agent/` displays profiling output but does not import `perf/` directly. Always import from `src.protocol` (the facade), not `src.protocol.schema` directly.

### Agent modules

- `cli.py` — CLI subcommands (`context`, `submit`, `poll`, `judge`, `baseline`, `revert`, `build-log`, `status`, `hints`) for Claude Code
- `hints.py` — architecture-specific optimization hints lookup (supports topics: optimization, perf-counters)
- `loop.py` — interactive iteration loop (manual fallback)
- `git_ops.py` — git subprocess wrappers (`GIT_TIMEOUT=60`), `record_result_or_revert()`, `full_revert()`, `force_push_submodule()`
- `campaign.py` — `CampaignConfig` TypedDict, `load_campaign()`
- `strategy.py` — `format_context()`, `validate_change()`, `extract_profile_summary()`
- `history.py` — TSV-based results/failures tracking
- `metric.py` — `compare_metric()`, `below_threshold()`, `Direction` Literal type
- `protocol.py` — request creation (`create_request()`), sequence numbering, `poll_for_completion()`, `find_request_by_seq()`

### Runner modules

- `service.py` — main polling loop, `execute_request()` orchestrates build→test→push
- `build.py` — meson + ninja build orchestration
- `testpmd.py` — PTY-based testpmd execution and throughput parsing (needs pseudo-TTY because testpmd buffers stdout without one)
- `execute.py` — DTS test execution
- `protocol.py` — git commit/push with retry, `claim()`, `update_status()`, `fail()`

### Configuration

- `config/campaign.toml` — what to optimize (metric, goal, DPDK scope, test backend)
- `config/runner.toml` — where to build/test (paths, PCI addresses, lcores, timeouts). Gitignored; copy from `runner.toml.example`
- `pyelftools` in dependencies is required by DPDK's meson build, not by this project's Python code

### Key types

- `StatusLiteral` — `Literal["pending", "claimed", "building", "running", "completed", "failed"]`
- `CampaignConfig` — TypedDict hierarchy matching campaign TOML structure
- `Direction` — `Literal["maximize", "minimize"]`
- Result dataclasses: `BuildResult`, `TestpmdResult`, `DtsResult` — all have `success`, `error`, `duration_seconds`

## Agent mode (Claude Code)

Read `program.md` for autonomous optimization instructions. The agent uses CLI subcommands (`uv run autosearch context/submit/poll/judge`) to interact with the remote runner. Start with: "read program.md and start experimenting".

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

The active sprint is set in `config/campaign.toml` under `[sprint] name = "..."`. Use `uv run autosearch sprint init <name>` to create a new sprint. Sprint names must match `YYYY-MM-DD-slug` format.

Never commit test or scratch request files — they will pollute the real results history. Tests should use `tmp_path` fixtures, not sprint directories.

## Pull requests

Before creating a PR, run the `markdown-doc-reviewer` agent on the branch to verify that documentation (README, CLAUDE.md, docstrings, inline comments) is consistent with code changes. Fix any findings before opening the PR.

## Style

- Python 3.13, `from __future__ import annotations` in every file
- 100-char line length, ruff for lint+format
- All subprocess calls must include `timeout=` parameter
- Use `src.protocol` facade for imports, not `src.protocol.schema`
- `pyelftools` must stay in dependencies — do not remove it
