# Agent Guide

The agent runs on your development workstation. It manages the optimization
loop: proposing DPDK changes, submitting test requests, and tracking results.

## Prerequisites

- Python 3.13+
- [uv](https://docs.astral.sh/uv/)
- DPDK submodule initialized (`git submodule update --init`)
- `ANTHROPIC_API_KEY` or `OPENROUTER_API_KEY` environment variable (autonomous mode only)

## Installation

```bash
uv sync
```

This installs the `autosearch` CLI entry point.

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
| `[goal]` | `description` | Freeform text injected into the Claude prompt |
| `[dpdk]` | `submodule_path` | Path to the DPDK submodule (default: `"dpdk"`) |
| `[dpdk]` | `optimization_branch` | Branch in submodule for good changes (default: `"autosearch/optimize"`) |
| `[dpdk]` | `scope` | Source paths the agent may modify (relative to submodule) |
| `[profiling]` | `enabled` | Include profiling summary in results sent to the agent (default: `false`) |

## Interactive mode

Default usage. You make changes in the DPDK submodule, commit them, and the
agent submits a test request.

```bash
uv run autosearch
```

Example session:

```
============================================================
Campaign: testpmd-throughput  |  Metric: throughput_mpps (maximize)
Iterations: 0/50  |  Best: —
Scope: lib/, drivers/net/, app/test-pmd/
============================================================

Make your DPDK changes in the submodule, commit them, then press Enter.
Type 'quit' to stop the loop.
>
Describe this change: increase rx burst size in testpmd
Request 0001 submitted. Polling for results...
Request 0001 completed. Metric: 14.72
```

Use `--dry-run` to skip git push (local testing only — the runner won't see
the request).

## Autonomous mode

Claude proposes changes based on iteration history and campaign scope. You
review each proposal before it's applied.

```bash
export ANTHROPIC_API_KEY=sk-...
uv run autosearch --autonomous
```

The agent shows Claude's proposal and prompts `Apply this change? [y/N/quit]`.

To apply a proposal:
1. Read Claude's description of which files to modify and what to change.
2. Make the edits manually in the `dpdk/` submodule.
3. Commit the changes inside `dpdk/` (`git -C dpdk commit -am "..."`).
4. Press `y` at the prompt.

The agent then submits the request and polls for results. Press `N` to skip
the proposal without applying it; Claude will propose something different next
iteration. Use `--dry-run` to test locally.

## CLI reference

| Flag | Description |
|------|-------------|
| `--campaign <path>` | Path to campaign TOML config (default: `config/campaign.toml`) |
| `--dry-run` | Skip git push — local testing only |
| `--autonomous` | Use Claude API for automated change proposals |
| `--provider` | API provider: `anthropic` (default) or `openrouter` |
| `--log-level` | Log level: `debug`, `info`, `warning`, `error` |
| `--log-file <path>` | Also write logs to a file |

## How results are tracked

Each iteration appends a row to `results.tsv` with columns:

- `sequence` — zero-padded iteration number
- `timestamp` — ISO 8601 UTC
- `dpdk_commit` — DPDK submodule HEAD at time of request
- `metric_value` — extracted metric (empty if failed/timed out)
- `status` — `completed`, `failed`, `timed_out`, or `dry_run`
- `description` — user-provided or Claude-generated change description

Failed attempts are recorded in `failures.tsv` with columns:

- `timestamp` — ISO 8601 UTC
- `dpdk_commit` — DPDK submodule HEAD of the reverted change
- `metric_value` — measured value that didn't improve
- `description` — change description
- `diff_summary` — summary of the reverted diff

Request JSON files in `requests/` follow the naming pattern
`{seq:04d}_{isodate}.json` and track the full lifecycle:
`pending -> claimed -> building -> running -> completed|failed`.

## Optimization branch

On startup, the agent creates an `autosearch/optimize` branch in the DPDK
submodule (configurable via `[dpdk].optimization_branch`). All proposed
changes are committed to this branch.

After each measurement:
- **Metric improves**: the commit is kept and the submodule pointer is updated
- **Metric worsens or stays flat**: the commit is reverted (`git reset --hard
  HEAD~1`) and the failed attempt is recorded in `failures.tsv`

In autonomous mode, recent failures are included in the Claude prompt so it
avoids repeating failed approaches.

## Troubleshooting

**"No submodule change detected"**
Commit your changes inside `dpdk/` before pressing Enter. The agent checks
whether the submodule pointer has changed relative to the last commit.

**Request timed out**
Increase `agent.timeout_minutes` in `config/campaign.toml`. The default is 60
minutes. Check the runner logs if timeouts are frequent.

**"anthropic package required"**
Install with `uv add anthropic` or run `uv sync` (it's already a project
dependency).
