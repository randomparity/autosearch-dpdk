# Autoforge — Doc Reviewer Memory

## Project-specific terminology
- "autoforge" is both the repo name and the CLI command/package name
- Always "testpmd" (lowercase), never "TestPMD" or "Testpmd"
- "agent" = workstation process; "runner" = lab machine process
- "submodule" = projects/dpdk/repo git submodule
- "request JSON" or "request file", not "job" or "task"
- "bi-directional Mpps" for the throughput metric
- Per-sprint optimization branches: sprint init stamps "autoforge/{sprint-name}" into campaign.toml automatically (feat 7e18b60). No shared "autoforge/optimize" branch — that is now stale terminology. campaign.toml.example uses placeholder "autoforge/YYYY-MM-DD-slug".

## Doc inventory (as of 2026-03-26, branch docs/walkthrough-fixes)
- README.md — project overview, quick-start, layout, dev commands
- CLAUDE.md — AI agent guidance (architecture, commands, style rules)
- docs/agent.md — agent setup, campaign config, CLI ref, history, troubleshooting
- docs/runner.md — runner setup, config table, testpmd/DTS backends, systemd, troubleshooting
- docs/plugin-sdk.md — plugin authoring guide
- projects/dpdk/runner.toml.example — authoritative runner config template for DPDK
- projects/dpdk/sprints/2026-03-25-ppc64le-mem-alignment/docs/summary.md — sprint retrospective
- projects/dpdk/sprints/2026-03-23-memif-ppc64le/docs/summary.md — sprint 001 retrospective

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

## Confirmed resolved (as of docs/walkthrough-fixes)
- docs/agent.md CLI table now includes finale, summarize, sprint init --from, submit -t/--tags
- results.tsv column is `source_commit` (confirmed in history.py COLUMNS) — doc now correct
- failures.tsv column is `source_commit` (confirmed in history.py FAILURE_COLUMNS) — doc now correct
- Protocol flow in CLAUDE.md and agent.md now includes built/deploying/deployed — correct
- StatusLiteral in CLAUDE.md now includes all 9 values — correct
- README.md pointer.py description accurate; campaign.py description accurate
- runner/protocol.py CLAUDE.md description accurate (complete_request added, returns None)
- `[paths].dpdk_src` is the correct key (not `source`) — runner.md and plugin-sdk.md config example both now use `dpdk_src`

## Confirmed resolved (as of docs/walkthrough-fixes — after commits 879110f + 8dd86d6)
1. README.md typo "erformance" → "performance" — FIXED
2. docs/agent.md "On startup, the agent creates an autoforge/optimize branch" — FIXED
3. docs/plugin-sdk.md configure() now uses `ProjectConfig` type — FIXED
4. docs/agent.md `[platform] arch` now in campaign config table — FIXED
5. docs/runner.md perf-record config path fixed: "Enable in projects/dpdk/perfs/perf-record.toml" — FIXED
6. docs/runner.md testpmd PCI/lcore path fixed: "projects/dpdk/tests/testpmd-memif.toml" — FIXED
7. docs/runner.md `autoforge doctor --role runner` advisory added — FIXED
8. docs/agent.md `--dry-run` description clarified — FIXED
9. sprint summary 2026-03-25: autosearch/optimize ref updated with clarifying note — FIXED

## Remaining doc gaps after commit 7e18b60 (per-sprint branch feature)
1. docs/agent.md line 54: optimization_branch description says `campaign.toml.example` sets `"autoforge/optimize"` — STALE. Template placeholder is now `"autoforge/YYYY-MM-DD-slug"` and sprint init stamps the real name automatically.
2. docs/agent.md line 142: "Optimization branch" section says default is `autoforge/optimize` and shows `checkout -b autoforge/optimize` — both stale. Branch is per-sprint, auto-stamped; example command should use `autoforge/{sprint-name}` pattern or be dropped.
3. CLAUDE.md line 135: sprint init description doesn't mention the auto-stamp of optimization_branch. Low priority but worth a note.

## Per-sprint branch feature behavior (confirmed in code, 7e18b60)
- sprint_branch_name(name) → f"autoforge/{name}" e.g. "autoforge/2026-03-26-my-sprint"
- sprint init stamps branch into campaign.toml at creation (template, --from, missing key all handled)
- loop.py: empty optimization_branch → SystemExit with clear error (no fallback to "autoforge/optimize")
- cli.py (submit/judge): empty optimization_branch → silently skips push (by design — "empty = skip")
- config/campaign.toml.example placeholder: "autoforge/YYYY-MM-DD-slug" (intentionally non-runnable)
- OPT_BRANCH_RE: canonical pattern is r"^autoforge/\d{4}-\d{2}-\d{2}-[a-z0-9][a-z0-9-]*$"

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
