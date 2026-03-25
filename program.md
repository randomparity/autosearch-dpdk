# autosearch-dpdk

Autonomous DPDK optimization via iterative experimentation.

## Setup

To set up a new experiment run, work with the user to:

1. **Read the campaign config**: `config/campaign.toml` defines the metric, scope, goal, and constraints.
2. **Check history**: `uv run autosearch context` shows current state, best result, recent attempts, profiling data, and past failures.
3. **Verify the DPDK submodule**: `ls dpdk/` should contain the DPDK source tree.
4. **Create a branch**: `git checkout -b autoresearch/<tag>` from current HEAD. The tag should be based on today's date (e.g. `mar25`).
5. **Establish baseline** (if no history): `uv run autosearch baseline` submits the unmodified DPDK for testing and waits for the result.
6. **Confirm and go**: Confirm setup looks good, then begin experimentation.

## Architecture

This is a two-machine system:

- **You** (the agent) edit DPDK source code on this workstation and push changes via git.
- **A remote runner** polls git, builds DPDK, runs testpmd, and pushes results back.

You cannot run testpmd locally — the runner machine has the hardware/setup. Communication is entirely via git: you push request JSON files, the runner pushes results back. Each experiment takes ~3 minutes (push + build + test + push back).

## What you CAN do

- Modify files in the DPDK submodule under the scoped paths from `campaign.toml` `[dpdk] scope`.
- Commit in the submodule, create request files, push via the CLI.
- Read any file in the repo for context.

## What you CANNOT do

- Modify `src/runner/`, `src/protocol/`, or `src/perf/` — these run on the remote machine.
- Run testpmd locally — there are no NICs or memif setup on this workstation.
- Add new Python dependencies.
- Change the memif wire protocol (`dpdk/drivers/net/memif/memif.h`).
- Change public DPDK API signatures or break other PMDs.

## CLI commands

All commands: `uv run autosearch <subcommand>`

| Command | What it does |
|---------|-------------|
| `uv run autosearch context` | Print campaign state, history, failures, and profiling data |
| `uv run autosearch submit -d "description"` | Validate submodule change, create request, commit, push |
| `uv run autosearch poll` | Poll git until latest request completes, print result |
| `uv run autosearch judge` | Compare result to best, keep or revert, record in TSV |
| `uv run autosearch baseline` | Submit baseline (no changes), wait for result |
| `uv run autosearch status` | Print latest request status without polling |

## Output format

After `poll` completes:
```
Request 0005 completed. Metric: 88.12
Profiling data (latest run):
  Hot functions:
   31.4%  eth_memif_rx
   18.5%  eth_memif_tx
   ...
```

After `judge`:
```
Improvement! 86.25 -> 88.12
```
or:
```
No improvement (85.10 vs best 86.25). Reverting.
```

## The experiment loop

LOOP FOREVER:

1. `uv run autosearch context` — read current state, profile hotspots, past failures
2. Read the DPDK source files in scope. Think about what to optimize based on the profiling data. Study the hot functions. Consider what prior failures tell you.
3. Edit the DPDK source files directly in `dpdk/`. Make a single, focused change.
4. Commit in the submodule:
   ```
   git -C dpdk add -A && git -C dpdk commit -m "short description of change"
   ```
5. `uv run autosearch submit -d "short description of change"` — creates the request and pushes
6. `uv run autosearch poll` — wait ~3 minutes for the runner to build and test
7. `uv run autosearch judge` — automatically keeps or reverts based on the metric
8. Repeat from step 1

## Error handling

- **Build failure**: `poll` will show the error. Read the build log, fix the code in the submodule, commit, and `submit` again.
- **Test failure**: `judge` will revert the submodule. Move on to a different approach.
- **Timeout**: Treat as failure. `judge` will revert. Consider simplifying the change.
- **Poll shows "still running"**: Wait and poll again. The runner may be building.
- **Multiple consecutive failures**: Re-read the source code. Review failures with `context`. Try a fundamentally different approach.

## Strategy tips

- **Start with the profile data.** Focus optimization effort where the samples are.
- **One change at a time.** Small, targeted changes are easier to evaluate.
- **Memory operations dominate.** For memif, `rte_memcpy` in rx/tx paths is the main cost. Prefetching, batch processing, and reducing copies have high impact.
- **Don't fight the compiler.** GCC and Clang are good at micro-optimization. Focus on algorithmic improvements and data access patterns.
- **Avoid UB.** DPDK runs with `-O3`; undefined behavior will be exploited by the optimizer.
- **Read past failures.** The `context` command shows what was tried and failed. Don't repeat failed approaches.

## NEVER STOP

Once the experiment loop has begun, do NOT pause to ask the human if you should continue. Do NOT ask "should I keep going?" or "is this a good stopping point?". The human might be asleep or away from the computer and expects you to continue working *indefinitely* until you are manually stopped. You are autonomous. If you run out of ideas, think harder — re-read the source code for new angles, try combining previous near-misses, try more radical changes. The loop runs until the human interrupts you, period.

As a guide: each experiment takes ~3 minutes, so you can run ~20/hour or ~160 overnight. Make them count.
