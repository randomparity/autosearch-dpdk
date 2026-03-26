# Code Reviewer Memory — autoresearch-dpdk

## Recurring Patterns Found

### perf/profile.py
- `-g` and `--call-graph` flags are mutually exclusive in perf record.
  `-g` expands to `--call-graph fp`. Correct: `--call-graph dwarf,16384` alone.
- `perf stat` return code is never checked after `communicate()`. Silent counter
  failure yields an empty counters dict without any error surfaced to the caller.
- No `--max-size` on `perf record` — unbounded perf.data growth on long runs.

### perf/arch/s390x.json
- `stalled_backend` mapped to `CPU_CYCLES:u` (total cycles, not stall cycles).
  Produces nonsensical backend_bound ratio near 1.0 if derived.

### perf/bpf/
- `cache-misses.bt` and `offcpu-stacks.bt` have no END block.
  bpftrace auto-prints maps in binary format, not Brendan Gregg folded-stack format.
- `offcpu-stacks.bt` leaks `@block_start` and `@block_stack` for processes that
  block but are never woken within the probe's lifetime.
- No bpftrace timeout bound — scripts run indefinitely if not interrupted.

### Import rule violations (recurring)
- runner/ importing from agent/ was fixed in refactor/continued-quality-improvements.
- Tests must use `autoforge.protocol` facade, not `autoforge.protocol.schema` directly.
  Violation in `tests/test_schema.py` line 9.
- `Direction` is now in `autoforge.protocol` (moved from `autoforge.agent.metric`).
  `autoforge/agent/loop.py` imports it from `autoforge.campaign` (re-export) instead of
  `autoforge.protocol` directly — violates import rule. Fix: add Direction to the
  autoforge.protocol import block in loop.py.

### autoforge/runner/base.py
- `DeployRunner.execute_phase` constructs a fake `BuildResult` with hardcoded
  build_dir from config — build artifacts are never actually propagated in
  split-runner topology.
- `fail()` is called without try/except in `recover_stale_requests()` and in the
  poll_loop `except Exception` handler. Since `update_status()` now raises RuntimeError
  on push failure (refactor/continued-quality-improvements), a git push failure in
  either of these locations crashes the runner completely. Fix: wrap fail() calls in
  try/except RuntimeError in both call sites.
- poll_loop: `execute_phase` is wrapped in try/except Exception, but the cleanup
  `fail()` call is outside that try block, so RuntimeError from fail() escapes to
  the outer KeyboardInterrupt handler and crashes the loop.

### autoforge/plugins/loader.py (feat/plugin-architecture)
- `spec.loader.exec_module(module)` has no try/except. Syntax errors or import
  failures in plugin files propagate uncaught into poll_loop.
  (Fixed in feat/issue-17: now wrapped with try/except in _load_python_class.)

### autoforge/runner/service.py (feat/plugin-architecture)
- `load_config` default `"config/runner.toml"` is relative to CWD — fails if
  runner starts from any other directory (systemd, cron). Same cwd-coupling
  pattern found in earlier branches.

### autoforge/runner/pyproject.toml (feat/plugin-architecture)
- Lives inside `autoforge/runner/` (unusual). `dependencies = []` is empty but
  `autoforge.runner.service:main` transitively imports `autoforge.agent` — the
  package cannot be installed standalone without `autoforge` as a dependency.

### autoforge/agent/project.py (feat/plugin-architecture)
- `init_project` dead if/else: both branches call `save_pointer(name, "")`.
  Condition `if POINTER_PATH.exists()` is meaningless — collapse to single call.

### projects/dpdk/builds/ (feat/plugin-architecture)
- `local.py` and `local-server.py` are exact duplicates (verified with diff).
  Campaign uses `build = "local-server"`. `local.py` is unreachable dead code.

### autoforge/protocol/schema.py (feat/plugin-architecture)
- `TestRequest.read()` can raise `OSError` or `json.JSONDecodeError`. Callers
  in `find_by_status` and `recover_stale_requests` only catch
  `(ValueError, KeyError, TypeError)` — missing `OSError` and `json.JSONDecodeError`.

### autoforge/agent/protocol.py
- `poll_for_completion` default `requests_dir: Path = Path("requests")` is a
  relative path. All callers pass it explicitly; default is misleading.
- `create_request` uses `# noqa: UP017` on `datetime.now(timezone.utc)`.
  Project targets Python 3.13 where `datetime.UTC` is available; noqa unnecessary.

### autoforge/logging_config.py
- Console handler logs to `sys.stdout`, not `sys.stderr`. Logging mixed with
  stdout (CLI prints, JSON results) will corrupt piped output.

### Sprint backward compatibility (feat/plugin-architecture)
- Old sprint `2026-03-23-memif-ppc64le/campaign.toml` uses `[dpdk]` section.
  New CLI reads `[project]`. `_source_path()` defaults to `"dpdk"` (wrong;
  submodule is now at `projects/dpdk/repo`).
- Old request files use `dpdk_commit`, `test_suites`, `backend` fields.
  `TestRequest.from_json()` raises `TypeError` on these. `find_by_status`
  catches `TypeError` and warns — no crash, but old requests are permanently
  unreadable by new protocol. Expected but undocumented.

### __init__.py files
- `autoforge/__init__.py`, `autoforge/runner/__init__.py`,
  `autoforge/agent/__init__.py`, `autoforge/protocol/__init__.py`:
  all missing `from __future__ import annotations` (CLAUDE.md: every file).

### projects/dpdk/tests/testpmd-memif.py
- `proc.wait(timeout=10)` at line 279 is not in a try/except.
  `subprocess.TimeoutExpired` propagates out of `_measure_throughput` uncaught.
  `finally` block in `run_testpmd` still calls `_ensure_stopped` for recovery,
  but the exception path is fragile.

### autoforge/perf/diff.py
- `verdict` for delta == 0.0 is "improved". Should be "neutral".
  (The unreachable case with default threshold=1.0; only triggered if threshold=0.0 is passed.)

### docs/arch-hints/ (feat/cpu-arch-hints)
- aarch64-perf-counters.md: N2 column labelled "Altra, Graviton 3" — wrong.
  Altra is N1-based; Graviton 3 is V1-based. Should be "Neoverse N2 (Yitian 710)".
- s390x.md L2 per core = "4 MB (z15/z16)" contradicts s390x-perf-counters.md
  which correctly says "32 MB unified" for z16.

## Architecture Notes
- ppc64le RHEL 8 / kernel 4.18: PMU event names are lowercase.
- ppc64le: 128B cache lines, 64KB base pages.
- `--call-graph lbr` is x86-only. Code correctly uses `dwarf`.
- Plugin system (feat/plugin-architecture): file-based discovery in
  `projects/<name>/{builds,deploys,tests,perfs}/`. `runtime_checkable` Protocol
  only checks attribute names, not signatures — wrong method signatures still pass.
- Campaign config resolution: `--campaign` flag > `AUTOFORGE_CAMPAIGN` env var >
  `.autoforge.toml` pointer → `projects/{project}/sprints/{sprint}/campaign.toml`
- Sprint campaign.toml format changed from `[dpdk]` to `[project]` in
  feat/plugin-architecture. Old sprints with `[dpdk]` are not backward-compatible.
- `autoforge` entry point: `autoforge.agent.cli:main`
- `autoforge-loop` entry point: `autoforge.agent.loop:main`
- `pyelftools` must stay in dependencies (DPDK meson build needs it).

## Test Coverage Gaps
- `profile_pid` success path untested.
- `_run_profiling` in testpmd-memif.py: zero coverage.
- `extract_profile_summary` with malformed JSON: no test.
- `diff_counters` with zero baseline (divide-by-zero guard path): no test.
- `find_request_by_seq` with malformed JSON: no test.
- `runner/protocol.py` `_git_commit_push`, `update_status`, `claim`, `fail`: now
  covered (13 new tests added in refactor/continued-quality-improvements).
- sprint.py `init_sprint`, `list_sprints`, `switch_sprint`: now tested
  (docs/walkthrough-fixes adds TestInitSprintBranchStamping, TestListSprints, etc.).

### fix/runner-setup branch patterns (reviewed 2026-03-26)
- `check_git_clean()` in git_ops.py: doesn't check `result.returncode`. If git fails,
  stdout is empty and the function silently passes. Pattern: always check returncode when
  not using `check=True`.
- `run_doctor()` in doctor.py:955 re-opens `.autoforge.toml` with raw `open()` after
  `check_pointer()` already validated it. No try/except around this second open.
- `cmd_finale()` in cli.py polls for result but does NOT record it in results.tsv or git-push
  the tsv. `cmd_baseline()` does both. Asymmetry means finale results are silently lost.
- `_WORKLOAD_RULES` in hints.py: defined as an empty list but never used. Dead code.
- `check_campaign()` in doctor.py validates `build` and `test` as required but not `deploy`.
  `load_pipeline()` raises ValueError if deploy is absent. Doctor under-reports.
- docs/runner.md: lines 59-64 table still references `[build]` and `[profiling]` as runner.toml
  sections, but they moved to plugin-specific sibling .toml files. Line 88 says
  "Configure in `projects/dpdk/runner.toml`" for `[testpmd]` which is now in
  `projects/dpdk/tests/testpmd-memif.toml`.
- Old results.tsv files (6-column header) from pre-branch sprints will silently drop `tags`
  when new 7-column rows are appended (7th value goes to `None` key via DictReader).
- Runner config split (fix/runner-setup): runner.toml moved to `projects/<project>/runner.toml`.
  Plugin configs now live as sibling .toml files next to each plugin .py.
  `local-server.py` renamed to `local.py`.

### feat/issue-17 patterns (reviewed 2026-03-26)
- Judge protocol uses TYPE_CHECKING guard for CampaignConfig and TestRequest — correct.
  No circular import. configure() is part of the protocol but never called in cmd_judge
  (load_judge passes no runner_config). configure() is dead for judge plugins loaded by
  the agent. This is a design gap — judge plugins cannot receive runner.toml config.
- Docstrings in _find_plugin_file, load_component, list_components still say
  "One of 'build', 'deploy', 'test', 'profiler'" — stale, missing 'judge'.
- loop.py does NOT integrate the judge plugin. Only cmd_judge (CLI path) does.
  The interactive fallback loop (autoforge-loop) always uses record_result_or_revert.
  This is an undocumented limitation.
- record_verdict returns None; record_result_or_revert returns bool.
  Inconsistency is harmless because the return value of record_result_or_revert is
  not used at either call site in cli.py or loop.py.
- No test for judge plugin when request status is "failed" (metric=None passed to judge).
- No test for judge plugin load failure surfacing through cmd_judge.
- check_campaign() in doctor.py does not validate the judge plugin key — consistent
  with existing behavior for profiler (also not validated), so not a regression.

### docs/walkthrough-fixes patterns (reviewed 2026-03-26)
- `sprint_branch_name()` in sprint.py doesn't validate the input sprint name.
  In practice only called after `validate_sprint_name()` in `init_sprint()`, so safe.
  `sprint_branch_name` is a pure derivation function — no guard needed.
- `OPT_BRANCH_RE` uses `$` not `\Z` — Python `re.match` with `$` matches before a
  trailing `\n`. Theoretical edge case; TOML string values don't contain embedded newlines.
  Same pattern used consistently with `SPRINT_NAME_RE` — acceptable.
- `check_optimization_branch` git error path (returncode != 0): emits no CheckResult.
  Intentional — the git check is advisory only. Silent failure acceptable here.
- `check_optimization_branch` runs git subprocess for both canonical AND non-canonical
  branch names — this is intentional and correct (advisory for all).
- Existing sprint campaign.tomls updated to `autoforge/optimize` (not per-sprint name).
  Doctor will warn on these with "does not match expected pattern". Expected and documented.
- docs/agent.md line 142: says `(default: autoforge/optimize)` but campaign.toml.example
  now uses placeholder `autoforge/YYYY-MM-DD-slug`. Minor inconsistency — placeholder
  is now the correct framing since there is no hardcoded default in code.
- `init_sprint` branch stamping uses `re.subn` on raw TOML text. Section-unaware:
  replaces `optimization_branch` anywhere in the file, not just under `[project]`.
  In practice the key only appears once under `[project]` in all templates.

## Project Conventions
- Import rule: always via `autoforge.protocol` facade, not `autoforge.protocol.schema`.
- runner/ and agent/ must not import from each other.
- All subprocess calls must include `timeout=` parameter.
- `from __future__ import annotations` in every file.
- Sprint layout: `projects/<project>/sprints/<name>/{requests/, docs/, campaign.toml, results.tsv}`
- Active sprint in `.autoforge.toml` at repo root.
- Plugin file stem = plugin name (e.g. `local.py` → `"local"`). Renamed from `local-server` in fix/runner-setup.
- `DEFAULT_REQUESTS_DIR` removed. All callers pass explicit Path arguments.
- `perf/results/` is gitignored.
- Runner config path resolution (fix/runner-setup): explicit arg > `AUTOFORGE_CONFIG` env > `.autoforge.toml` pointer → `projects/<project>/runner.toml`.

Notes:
- Always use absolute file paths.
- In final responses, share absolute file paths and include code snippets only when load-bearing.
- Do not use emojis.
- Do not use a colon before tool calls.
