# Agent Guide

The agent runs on your development workstation. It manages the optimization
loop: proposing source changes, submitting test requests, and tracking results.

## Quick start

```
read the active sprint's program.md and start experimenting
```

Paste that into Claude Code. It reads the sprint program, proposes changes,
submits them for testing, and iterates based on results.

## Prerequisites

- Python 3.13+
- [uv](https://docs.astral.sh/uv/)
- Source submodule initialized for your project (`git submodule update --init`)

## Installation

```bash
git clone --recurse-submodules <repo-url> autoforge
cd autoforge
make setup-agent
```

## Configuration

The agent reads configuration from `.autoforge.toml` (pointer to active
project/sprint) and the sprint's `campaign.toml`. Both are tracked in git
and shared across all systems.

String values support `${VAR}` for environment variables and `${REPO_ROOT}`
for repo-relative paths. Run `autoforge doctor --role agent` to validate
your setup.

Campaign settings are per-sprint at
`projects/<project>/sprints/<sprint>/campaign.toml`:

| Section | Key | Description |
|---------|-----|-------------|
| `[campaign]` | `name` | Campaign identifier |
| `[campaign]` | `max_iterations` | Stop after this many iterations (default: 50) |
| `[metric]` | `name` | Human-readable metric name |
| `[metric]` | `path` | Key path into results JSON (dot-separated for nested dicts) |
| `[metric]` | `direction` | `"maximize"` or `"minimize"` |
| `[metric]` | `threshold` | Minimum absolute improvement required to keep a change |
| `[metric]` | `comparison` | Baseline for keep/revert: `"peak"` (all-time best, default) or `"rolling_average"` (mean of last N) |
| `[metric]` | `comparison_window` | Number of recent results to average when `comparison = "rolling_average"` (default: 5) |
| `[agent]` | `poll_interval` | Seconds between polling for results (default: 30) |
| `[agent]` | `timeout_minutes` | Max wait for a single test run (default: 60) |
| `[goal]` | `description` | Freeform text injected into the agent prompt |
| `[project]` | `build` | Build plugin name (e.g. `"local"`) |
| `[project]` | `deploy` | Deploy plugin name (e.g. `"local"`) |
| `[project]` | `test` | Test plugin name (e.g. `"testpmd-memif"`) |
| `[project]` | `profiler` | Profiler plugin name (e.g. `"perf-record"`) |
| `[project]` | `submodule_path` | Path to the project's source submodule |
| `[project]` | `optimization_branch` | Branch for good changes (empty = skip branch push; set automatically by `sprint init` to `autoforge/{sprint-name}`) |
| `[project]` | `scope` | Source paths the agent may modify (relative to submodule) |
| `[profiling]` | `enabled` | Include profiling summary in results (default: `false`) |
| `[platform]` | `arch` | Target architecture for `autoforge hints` (e.g. `"ppc64le"`, `"x86_64"`). Required unless `--arch` is passed. |

## Interactive mode

For manual experimentation. You make changes in the project's source submodule, commit
them, and the loop submits a test request.

```bash
uv run autoforge-loop
```

Use `--dry-run` to skip git push (local testing only — the runner won't see
the request).

## Autonomous mode (Claude Code)

Autonomous mode is handled by Claude Code reading the active sprint's
`program.md`. Each sprint directory contains a `program.md` tailored to that
sprint's optimization goals. Claude Code uses the CLI subcommands (`context`,
`submit`, `poll`, `judge`) to interact with the remote runner.

## CLI reference

`autoforge` subcommands:

| Command | Description |
|---------|-------------|
| `autoforge context` | Print campaign state, history, failures, profiling data |
| `autoforge submit -d "description" [-t "tags"]` | Validate submodule change, create request, push (optional comma-separated experiment tags) |
| `autoforge poll` | Poll until latest request completes |
| `autoforge judge` | Compare result to best, keep or revert, record in TSV |
| `autoforge baseline` | Submit baseline (no changes) and poll for result |
| `autoforge finale` | Submit finale request (modified source, profiling disabled) |
| `autoforge summarize` | Generate sprint summary document from results history |
| `autoforge status` | Print latest request status without polling |
| `autoforge doctor` | Validate configuration setup |
| `autoforge doctor --role agent` | Agent-side configuration checks only |
| `autoforge sprint init <name> [--from <sprint>]` | Create a new sprint (`YYYY-MM-DD-slug`); optionally clone config from existing sprint |
| `autoforge sprint list` | List all sprints with iteration counts |
| `autoforge sprint active` | Print active sprint name |
| `autoforge sprint switch <name>` | Switch active sprint |
| `autoforge project init <name>` | Scaffold a new project |
| `autoforge project list` | List all projects |
| `autoforge project switch <name>` | Switch active project |
| `autoforge revert` | Revert last source submodule commit and force-push fork |
| `autoforge logs --seq N` | Print logs for a request; auto-detects failed phase (`-s N` short form) |
| `autoforge logs --seq N --phase build` | Print logs for a specific phase (`build`, `deploy`, or `test`) |
| `autoforge logs --seq N --grep STR --tail N` | Filter log lines by substring, show last N |
| `autoforge build-log --seq N` | Alias for `logs --phase build` (kept for backward compatibility) |
| `autoforge inspect --seq N` | Show full request details: timeline, plugins, metric, all log snippets |
| `autoforge inspect --seq N --json` | Output raw request JSON |
| `autoforge sysinfo --role agent` | Collect and save system info to sprint docs dir |
| `autoforge hints` | Show arch optimization checklist for the target architecture |
| `autoforge hints --list` | List available hint topics |
| `autoforge hints --topic perf-counters` | Show PMU performance counter reference |

Global flags (before the subcommand):

| Flag | Description |
|------|-------------|
| `--campaign <path>` | Path to campaign TOML (overrides `.autoforge.toml` pointer) |
| `--dry-run` | Skip git push — applies to `submit`, `judge`, `baseline`, `finale`, `revert` |

For interactive manual iteration: `uv run autoforge-loop [--dry-run]`

## How results are tracked

Each iteration appends a row to
`projects/<project>/sprints/<name>/results.tsv` with columns:

- `sequence` — zero-padded iteration number
- `timestamp` — ISO 8601 UTC
- `source_commit` — source submodule HEAD at time of request
- `metric_value` — extracted metric (empty if failed/timed out)
- `status` — `completed`, `failed`, `timed_out`, or `dry_run`
- `description` — user-provided or agent-generated change description

Failed attempts are recorded in `sprints/<name>/failures.tsv` (created on
first failure) with columns:

- `timestamp` — ISO 8601 UTC
- `source_commit` — source submodule HEAD of the reverted change
- `metric_value` — measured value that didn't improve
- `description` — change description
- `diff_summary` — summary of the reverted diff

Request JSON files in `sprints/<name>/requests/` follow the naming pattern
`{seq:04d}_{isodate}.json` and track the full lifecycle:
`pending -> claimed -> building -> built -> deploying -> deployed -> running -> completed|failed`.

## Optimization branch

The agent commits all proposed changes to the branch named in
`[project].optimization_branch`, stamped automatically by `autoforge sprint init`
as `autoforge/{sprint-name}` (e.g. `autoforge/2026-04-01-my-sprint`). The branch
is created in the submodule on the first `submit` or `autoforge-loop` run.

Run `autoforge doctor --role agent` to verify the branch is configured and exists
in the submodule.

After each measurement:

- **Metric improves**: the commit is kept and the submodule pointer is updated
- **Metric worsens or stays flat**: the commit is reverted (`git reset --hard
  HEAD~1`), the submodule's optimization branch is force-pushed to keep the
  fork in sync, and the failed attempt is recorded in `failures.tsv`

Recent failures are included in the `context` output so the agent avoids
repeating failed approaches.

## Troubleshooting

**"No submodule change detected"**
Commit your changes inside the source submodule before pressing Enter. The agent
checks whether the submodule pointer has changed relative to the last commit.

**Request timed out**
Increase `agent.timeout_minutes` in the sprint's `campaign.toml`. The default
is 60 minutes. Check the runner logs if timeouts are frequent.
