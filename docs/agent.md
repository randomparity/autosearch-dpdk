# Agent Guide

The agent runs on your development workstation. It manages the optimization
loop: proposing DPDK changes, submitting test requests, and tracking results.

## Prerequisites

- Python 3.13+
- [uv](https://docs.astral.sh/uv/)
- DPDK submodule initialized (`git submodule update --init`)

## Installation

```bash
uv sync
```

This installs the `autosearch` and `autosearch-loop` CLI entry points.

## Campaign configuration

All campaign settings live in `config/campaign.toml`. The agent reads this file
on startup (override with `--campaign <path>`).

| Section | Key | Description |
|---------|-----|-------------|
| `[campaign]` | `name` | Campaign identifier |
| `[campaign]` | `max_iterations` | Stop after this many iterations (default: 50) |
| `[metric]` | `name` | Human-readable metric name |
| `[metric]` | `path` | Key path into results JSON (dot-separated for nested dicts) |
| `[metric]` | `direction` | `"maximize"` or `"minimize"` |
| `[metric]` | `threshold` | Stop early if improvement falls below this value |
| `[test]` | `backend` | Test backend: `"testpmd"` (default) or `"dts"` |
| `[test]` | `test_suites` | List of test suite names to run |
| `[test]` | `perf` | Enable performance mode (`true`/`false`) |
| `[agent]` | `poll_interval` | Seconds between polling for results (default: 30) |
| `[agent]` | `timeout_minutes` | Max wait for a single test run (default: 60) |
| `[goal]` | `description` | Freeform text injected into the agent prompt |
| `[dpdk]` | `submodule_path` | Path to the DPDK submodule (default: `"dpdk"`) |
| `[dpdk]` | `optimization_branch` | Branch in submodule for good changes (default: `"autosearch/optimize"`) |
| `[dpdk]` | `scope` | Source paths the agent may modify (relative to submodule) |
| `[profiling]` | `enabled` | Include profiling summary in results (default: `false`) |
| `[sprint]` | `name` | Active sprint name; set by `autosearch sprint init` or `sprint switch` |

## Interactive mode

For manual experimentation. You make changes in the DPDK submodule, commit
them, and the loop submits a test request.

```bash
uv run autosearch-loop
```

Use `--dry-run` to skip git push (local testing only — the runner won't see
the request).

## Autonomous mode (Claude Code)

Autonomous mode is handled by Claude Code reading `program.md` directly.
Claude Code uses the CLI subcommands (`context`, `submit`, `poll`, `judge`)
to interact with the remote runner. See `program.md` for the full workflow.

## CLI reference

`autosearch` subcommands:

| Command | Description |
|---------|-------------|
| `autosearch context` | Print campaign state, history, failures, profiling data |
| `autosearch submit -d "description"` | Validate submodule change, create request, push |
| `autosearch poll` | Poll until latest request completes |
| `autosearch judge` | Compare result to best, keep or revert, record in TSV |
| `autosearch baseline` | Submit baseline (no changes) and poll for result |
| `autosearch status` | Print latest request status without polling |
| `autosearch sprint init <name>` | Create a new sprint (`YYYY-MM-DD-slug`) |
| `autosearch sprint list` | List all sprints with iteration counts |
| `autosearch sprint active` | Print active sprint name |
| `autosearch sprint switch <name>` | Switch active sprint in `campaign.toml` |
| `autosearch revert` | Revert last DPDK submodule commit and force-push fork |
| `autosearch build-log --seq N` | Print formatted build log for request N (`-s N` short form) |

Global flags (before the subcommand):

| Flag | Description |
|------|-------------|
| `--campaign <path>` | Path to campaign TOML (default: `config/campaign.toml`) |
| `--dry-run` | Skip git push (local testing only) |

For interactive manual iteration: `uv run autosearch-loop [--dry-run]`

## How results are tracked

Each iteration appends a row to `sprints/<name>/results.tsv` with columns:

- `sequence` — zero-padded iteration number
- `timestamp` — ISO 8601 UTC
- `dpdk_commit` — DPDK submodule HEAD at time of request
- `metric_value` — extracted metric (empty if failed/timed out)
- `status` — `completed`, `failed`, `timed_out`, or `dry_run`
- `description` — user-provided or agent-generated change description

Failed attempts are recorded in `sprints/<name>/failures.tsv` (created on
first failure) with columns:

- `timestamp` — ISO 8601 UTC
- `dpdk_commit` — DPDK submodule HEAD of the reverted change
- `metric_value` — measured value that didn't improve
- `description` — change description
- `diff_summary` — summary of the reverted diff

Request JSON files in `sprints/<name>/requests/` follow the naming pattern
`{seq:04d}_{isodate}.json` and track the full lifecycle:
`pending -> claimed -> building -> running -> completed|failed`.

## Optimization branch

On startup, the agent creates an `autosearch/optimize` branch in the DPDK
submodule (configurable via `[dpdk].optimization_branch`). All proposed
changes are committed to this branch.

After each measurement:
- **Metric improves**: the commit is kept and the submodule pointer is updated
- **Metric worsens or stays flat**: the commit is reverted (`git reset --hard
  HEAD~1`), the submodule's optimization branch is force-pushed to keep the
  fork in sync, and the failed attempt is recorded in `failures.tsv`

Recent failures are included in the `context` output so the agent avoids
repeating failed approaches.

## Troubleshooting

**"No submodule change detected"**
Commit your changes inside `dpdk/` before pressing Enter. The agent checks
whether the submodule pointer has changed relative to the last commit.

**Request timed out**
Increase `agent.timeout_minutes` in `config/campaign.toml`. The default is 60
minutes. Check the runner logs if timeouts are frequent.
