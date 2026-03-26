# Autoforge — Doc Reviewer Memory

## Project-specific terminology
- "autoforge" is both the repo name and the CLI command/package name
- Always "testpmd" (lowercase), never "TestPMD" or "Testpmd"
- "agent" = workstation process; "runner" = lab machine process
- "submodule" = projects/dpdk/repo git submodule
- "request JSON" or "request file", not "job" or "task"
- "bi-directional Mpps" for the throughput metric
- "autoforge/optimize" is the default optimization branch name (set in campaign.toml template, not code default — code defaults to "")

## Doc inventory (as of 2026-03-26, branch fix/runner-setup)
- README.md — project overview, quick-start, layout, dev commands
- CLAUDE.md — AI agent guidance (architecture, commands, style rules)
- docs/agent.md — agent setup, campaign config, CLI ref, history, troubleshooting
- docs/runner.md — runner setup, config table, testpmd/DTS backends, systemd, troubleshooting
- docs/plugin-sdk.md — NEW (this branch): plugin authoring guide, 547 lines
- projects/dpdk/runner.toml.example — authoritative runner config template for DPDK
- projects/dpdk/sprints/2026-03-25-ppc64le-mem-alignment/docs/summary.md — sprint retrospective

## Plugin architecture (fix/runner-setup — confirmed)
- Plugins are Python files under projects/<project>/{builds,deploys,tests,perfs}/
- autoforge_dpdk/ package does NOT exist — CLAUDE.md references to it are stale (blocker)
- Actual DPDK plugins: projects/dpdk/builds/local.py, deploys/local.py, tests/testpmd-memif.py, tests/dts-mlx5.py, perfs/perf-record.py
- The runner reads dpdk source path from [paths].dpdk_src (NOT [paths].source)
- plugin-sdk.md framework config example erroneously uses `source = ...` — should be `dpdk_src`
- deploys/local.py has no sibling .toml.example (pass-through needs none)

## CLI commands (confirmed in code, fix/runner-setup)
All registered: context, status, poll, judge, baseline, finale, revert, build-log, hints, summarize, doctor, submit, sprint (init/list/active/switch), project (init)
- submit accepts -t/--tags flag (comma-separated experiment tags)
- sprint init accepts --from flag (clone from existing sprint)
- finale and summarize exist in code but are NOT in agent.md CLI table
- sprint init --from exists but is NOT in agent.md CLI table
- submit -t/--tags is NOT in agent.md CLI table

## Known doc gaps (desloppify/code-health — confirmed fixed from fix/runner-setup)
- README.md, CLAUDE.md, docs/agent.md CLI table gaps were all fixed in main (merged fix/runner-setup)
- plugin-sdk.md `source =` → `dpdk_src` fix: verify on next review

## New doc gaps found (desloppify/code-health)
1. CLAUDE.md line 78: `git_ops.py` description says `GIT_TIMEOUT=60` — GIT_TIMEOUT was moved to `autoforge/protocol/__init__.py`, git_ops.py now imports it from there
2. docs/agent.md line 120: results.tsv column documented as `dpdk_commit` but actual column name is `source_commit` (in history.py COLUMNS and sprint.py RESULTS_COLUMNS)
3. docs/agent.md line 129: failures.tsv column documented as `dpdk_commit` but actual column name is `source_commit` (in history.py FAILURE_COLUMNS)
4. docs/agent.md line 136: protocol flow summary says `pending -> claimed -> building -> running -> completed|failed` — omits built/deploying/deployed (same gap as CLAUDE.md, both still present on this branch)
5. CLAUDE.md line 111: StatusLiteral shown as `Literal["pending", "claimed", "building", "running", "completed", "failed"]` — omits "built", "deploying", "deployed" (these exist in schema.py and protocol/__init__.py)
6. pyproject.toml [dependency-groups] agent group is now empty — `make setup-agent` note in docs/agent.md (line 24) and memory note about matplotlib are now stale; matplotlib was removed

## New doc gaps found (refactor/continued-quality-improvements)
1. CLAUDE.md line 63: campaign.py description says "pointer load/save" — pointer ops moved to autoforge/pointer.py; campaign.py now provides typed accessor functions
2. CLAUDE.md line 82: metric.py listed as defining `Direction` Literal type — Direction moved to autoforge/protocol/schema.py, re-exported via autoforge.protocol; metric.py now imports it from there
3. CLAUDE.md line 88: runner/protocol.py described as "git commit/push with retry, claim(), update_status(), fail()" — update_status() now returns None (not bool) and raises RuntimeError on push failure; new complete_request() function added; fail() return changed to None
4. CLAUDE.md line 114: Direction listed under Key types without canonical location — now lives in autoforge.protocol (via schema.py)
5. README.md line 52: campaign.py described as "Pointer load/save, campaign resolution" — needs update to reflect split into pointer.py

## Confirmed accurate facts (desloppify/code-health)
- `uv run autoforge <subcommand>` is the correct CLI invocation
- `make setup-agent` installs --group dev --group agent (agent group is now empty — matplotlib removed)
- `make setup-runner` installs --group dev only
- Runner config resolution: explicit arg > AUTOFORGE_CONFIG env > .autoforge.toml pointer — correct in runner.md
- testpmd-memif config table in runner.md is accurate and complete
- poll_for_completion `requests_dir` is now a required keyword-only param (not optional positional)
- GIT_TIMEOUT is now defined in autoforge/protocol/schema.py, re-exported by autoforge/protocol/__init__.py
- find_pending is gone from runner/protocol.py; replaced by find_by_status(dir, status)
- _poll_and_record helper exists in cli.py (internal, not public API — no doc impact)
- sprint.py functions (active_sprint_name, sprint_dir, etc.) take no `campaign` param — correct

## refactor/continued-quality-improvements — key changes confirmed
- autoforge/pointer.py NEW: REPO_ROOT, POINTER_PATH, PointerConfig, load_pointer(), save_pointer()
- autoforge/campaign.py: lost pointer ops (moved to pointer.py); gained 18 typed accessor functions
- autoforge/protocol/schema.py: GIT_TIMEOUT and Direction moved here from protocol/__init__.py and metric.py
- autoforge/agent/git_ops.py: ResultContext dataclass added; record_result_or_revert() now takes ctx: ResultContext instead of 9 flat params
- autoforge/agent/strategy.py: validate_change() → has_submodule_change(); resolve_arch() removed (replaced by campaign.platform_arch)
- autoforge/agent/hints.py: hints_summary() → hints_file_ref(); resolve_arch() removed
- autoforge/runner/protocol.py: update_status() now returns None and raises RuntimeError; complete_request() new function; fail() returns None
- autoforge/perf/profile.py: ProfileResult → PerfCaptureResult (plugin ProfileResult in protocols.py is unchanged)
- autoforge/perf/analyze.py: ProfileSummary TypedDict added; summarize() return type tightened

## Style conventions observed
- Heading style: Title Case for top-level (#), sentence case acceptable for lower levels
- Tables: pipe-delimited, consistent column alignment
- Code blocks: `bash` language hints; `toml` for config examples; `python` for code
- Bullet markers: `-` throughout
- No TOC in any doc (acceptable given current lengths)
- sprint summary format: Overview table, Throughput graph, Accepted Patches table, Rejected Experiments table, Build/Test Failures table, Appendices
