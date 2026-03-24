# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
uv sync --group dev          # install deps + dev tools
uv run pytest -q             # run all tests (95 tests)
uv run pytest tests/test_schema.py -q                 # run one test file
uv run pytest tests/test_schema.py::TestSerialization  # run one test class
uv run ruff check src/ tests/                          # lint
uv run ruff format src/ tests/                         # format
uv run autosearch --dry-run                            # run agent locally (no git push)
```

## Architecture

Two-process system: an **agent** (workstation) proposes DPDK source changes and a **runner** (lab machine with NICs) builds and measures throughput. They communicate via git — JSON request files in `requests/`, results pushed back.

### Protocol flow

```
pending → claimed → building → running → completed
                                       → failed (from any state)
```

`TestRequest` dataclass in `src/protocol/schema.py` is the shared contract. Both sides serialize it as JSON files named `{seq:04d}_{timestamp}.json`. Status transitions are enforced by `VALID_TRANSITIONS`.

### Package boundaries

```
src/protocol/    Shared: TestRequest, status constants, StatusLiteral, extract_metric
src/agent/       Workstation: optimization loop, Claude API, git ops, history tracking
src/runner/      Lab machine: build DPDK, run testpmd/DTS, push results
```

**Import rule:** `agent/` and `runner/` both import from `protocol/`, never from each other. Always import from `src.protocol` (the facade), not `src.protocol.schema` directly.

### Agent modules

- `loop.py` — CLI entry point, interactive iteration loop
- `autonomous.py` — Claude API loop, `_record_result_or_revert` (shared by both loops), `_below_threshold`
- `git_ops.py` — all git subprocess wrappers (`GIT_TIMEOUT=60` on every call)
- `campaign.py` — `CampaignConfig` TypedDict matching `config/campaign.toml`
- `strategy.py` — `format_context()` for prompt building, `validate_change()` for submodule diff
- `history.py` — TSV-based results/failures tracking
- `metric.py` — `compare_metric()` with `Direction` Literal type

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

## Working with request/result files

`requests/*.json`, `results.tsv`, and `failures.tsv` are live performance tracking data used by the optimization loop. Never commit test or scratch request files — they will pollute the real results history. If you create request files during development or testing, delete them before committing. Tests should use `tmp_path` fixtures, not the repo's `requests/` directory.

## Style

- Python 3.13, `from __future__ import annotations` in every file
- 100-char line length, ruff for lint+format
- All subprocess calls must include `timeout=` parameter
- Use `src.protocol` facade for imports, not `src.protocol.schema`
- `pyelftools` must stay in dependencies — do not remove it
