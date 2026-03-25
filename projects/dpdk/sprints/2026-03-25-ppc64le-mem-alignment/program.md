# autoforge-dpdk — NUMA-Local Memory Alignment Sprint

Autonomous DPDK optimization: ensure memory allocations are NUMA-local to the forwarding lcores on POWER9.

## Setup

To set up a new experiment run, work with the user to:

1. **Read the campaign config**: `config/campaign.toml` defines the metric, scope, goal, and constraints.
2. **Check history**: `uv run autoforge context` shows current state, best result, recent attempts, profiling data, and past failures.
3. **Verify the DPDK submodule**: `ls projects/dpdk/repo/` should contain the DPDK source tree.
4. **Ensure the submodule optimization branch exists**: `git -C projects/dpdk/repo checkout -b autoforge/optimize 2>/dev/null || git -C projects/dpdk/repo checkout autoforge/optimize`. All DPDK changes accumulate on this branch inside the submodule.
5. **Establish baseline** (if no history): `uv run autoforge baseline` submits the unmodified DPDK for testing and waits for the result.
6. **Confirm and go**: Confirm setup looks good, then begin experimentation.

All artifacts (requests, results, failures, docs) are stored under `projects/dpdk/sprints/<name>/`.

## Architecture

This is a two-machine system:

- **You** (the agent) edit DPDK source code on this workstation and push changes via git.
- **A remote runner** polls git, builds DPDK, runs testpmd, and pushes results back.

You cannot run testpmd locally — the runner machine has the hardware/setup. Communication is entirely via git: you push request JSON files, the runner pushes results back. Each experiment takes ~3 minutes (push + build + test + push back).

## What you CAN do

- Modify files in the DPDK submodule under the scoped paths from `campaign.toml` `[project] scope`:
  - `drivers/net/memif/` — the memif PMD (rx/tx burst functions, descriptor handling)
  - `app/test-pmd/` — testpmd forwarding application
  - `lib/eal/ppc/` — POWER-specific EAL: rte_memcpy, rte_prefetch, rte_atomic, rte_pause
  - `lib/eal/include/` — architecture-generic EAL headers
  - `lib/eal/common/` — EAL memory subsystem: malloc_heap, malloc_elem, eal_common_memory, memzone
  - `lib/ring/` — lock-free ring queue (underlies mempool enqueue/dequeue)
  - `lib/mbuf/` — mbuf alloc/free, metadata, pool ops
  - `lib/mempool/` — mempool cache, pool operations
- Commit in the submodule, create request files, push via the CLI.
- Read any file in the repo for context.

## What you CANNOT do

- Modify `autoforge/runner/`, `autoforge/protocol/`, or `autoforge/perf/` — these run on the remote machine.
- Run testpmd locally — there are no NICs or memif setup on this workstation.
- Add new Python dependencies.
- Change the memif wire protocol (`projects/dpdk/repo/drivers/net/memif/memif.h`).
- Change public DPDK API signatures or break other PMDs or platforms.
- Library changes must be guarded by `RTE_ARCH_PPC_64` ifdefs where they are
  architecture-specific. Generic changes must not regress other architectures.

## CLI commands

All commands: `uv run autoforge <subcommand>`

| Command | What it does |
|---------|-------------|
| `uv run autoforge context` | Print campaign state, history, failures, and profiling data |
| `uv run autoforge submit -d "description"` | Validate submodule change, create request, commit, push |
| `uv run autoforge poll` | Poll git until latest request completes, print result |
| `uv run autoforge judge` | Compare result to best, keep or revert, record in TSV |
| `uv run autoforge baseline` | Submit baseline (no changes), wait for result |
| `uv run autoforge status` | Print latest request status without polling |
| `uv run autoforge sprint init <name>` | Create a new sprint (YYYY-MM-DD-slug) |
| `uv run autoforge sprint list` | List all sprints with iteration counts |
| `uv run autoforge sprint active` | Print active sprint name |
| `uv run autoforge sprint switch <name>` | Switch active sprint in `campaign.toml` |
| `uv run autoforge revert` | Revert last DPDK submodule commit and force-push fork |
| `uv run autoforge build-log --seq N` | Print formatted build log for request sequence N |
| `uv run autoforge hints` | Show arch optimization checklist for the campaign's target architecture |
| `uv run autoforge hints --list` | List available hint topics (e.g., `optimization`, `perf-counters`) |
| `uv run autoforge hints --topic perf-counters` | Show PMU performance counter reference for the architecture |

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

1. `uv run autoforge context` — read current state, profile hotspots, past failures
2. Read the DPDK source files in scope. Think about what to optimize based on the profiling data. Study the hot functions. Consider what prior failures tell you.
3. Edit the DPDK source files directly in `projects/dpdk/repo/`. Make a single, focused change.
4. Commit in the submodule:
   ```
   git -C projects/dpdk/repo add -A && git -C projects/dpdk/repo commit -m "short description of change"
   ```
5. `uv run autoforge submit -d "short description of change"` — creates the request and pushes
6. `uv run autoforge poll` — wait ~3 minutes for the runner to build and test
7. `uv run autoforge judge` — automatically keeps or reverts based on the metric
8. Repeat from step 1

## Error handling

- **Build failure**: `poll` will show the error. Run `uv run autoforge build-log --seq N` to see the full formatted build log with error lines highlighted. Fix the code in the submodule, commit, and `submit` again.
- **Test failure**: `judge` will revert the submodule. Move on to a different approach.
- **Timeout**: Treat as failure. `judge` will revert. Consider simplifying the change.
- **Poll shows "still running"**: Wait and poll again. The runner may be building.
- **Multiple consecutive failures**: Re-read the source code. Review failures with `context`. Try a fundamentally different approach.

## Strategy: NUMA-local memory alignment on POWER9

### Starting point: arch_mem_object_align()

Begin by reading and understanding `arch_mem_object_align()` — this is where DPDK
decides how to align memory objects on a given architecture. On POWER9 with 128-byte
cache lines, alignment choices directly affect whether objects straddle cache lines
and whether they land in NUMA-local memory.

### The hypothesis

On POWER9 with 8 NUMA nodes, the forwarding lcores (96-103) reside on NUMA node 8.
If mbufs, mempools, descriptor rings, or shared structures are allocated from a
different NUMA node's memory, every packet touches remote memory — potentially
doubling latency per access.

### Key investigation areas

1. **EAL memory subsystem** (`lib/eal/common/`):
   - `eal_common_memory.c` — memseg initialization, hugepage mapping
   - `malloc_heap.c` — per-socket malloc heaps, `rte_malloc_socket()`
   - `malloc_elem.c` — element alignment within heaps
   - How hugepages are assigned to NUMA nodes at init time
   - Whether `--socket-mem` EAL parameter is respected for all allocations

2. **Mempool NUMA affinity** (`lib/mempool/`):
   - `rte_mempool_create()` takes a `socket_id` — verify memif uses the correct one
   - Per-lcore mempool cache: is the cache itself NUMA-local?
   - `rte_mempool_populate_default()` — where do backing hugepages come from?

3. **Memzone placement** (`lib/eal/common/`):
   - `rte_memzone_reserve()` — verify descriptor rings and shared structures
     specify the correct socket for the forwarding lcores
   - memif shared memory region: is it NUMA-aware?

4. **Object alignment for POWER9**:
   - 128-byte cache lines (vs 64B on x86): structures aligned to 64B straddle
     cache lines on POWER9, causing double the cache misses
   - `RTE_CACHE_LINE_SIZE` should be 128 on ppc64le — verify this is used
     consistently in mempool object sizing and ring element spacing
   - `__rte_cache_aligned` attribute: verify it expands to 128B on POWER9

5. **Physical memory topology**:
   - POWER9 SMT-4: each core has 4 hardware threads
   - lcores 96-103 map to 2 physical cores on NUMA node 8
   - Memory controllers on node 8 serve specific DIMM banks
   - Verify hugepage allocation lands on node 8's memory controllers

### What to look for in profiling data

- High `cache-misses` or `dTLB-load-misses` in the perf counters suggest memory
  is not local or alignment is wrong
- If `rte_mempool_get_bulk` / `rte_mempool_put_bulk` are hot, the mempool may be
  on the wrong NUMA node
- Backend-bound > 70% with low IPC suggests memory stalls — likely cross-NUMA

### General tips

- **One change at a time.** Small, targeted changes are easier to evaluate.
- **Don't fight the compiler.** Modern compilers at `-O3` are aggressive.
- **Avoid UB.** DPDK runs with `-O3`; undefined behavior will be exploited by the optimizer.
- **Guard arch-specific changes.** Use `#ifdef RTE_ARCH_PPC_64` for POWER-only optimizations in library code.
- **Read past failures.** The `context` command shows what was tried and failed.
- **Read arch-specific hints.** Run `uv run autoforge hints` for the target architecture's optimization checklist.

## NEVER STOP

Once the experiment loop has begun, do NOT pause to ask the human if you should continue. Do NOT ask "should I keep going?" or "is this a good stopping point?". The human might be asleep or away from the computer and expects you to continue working *indefinitely* until you are manually stopped. You are autonomous. If you run out of ideas, think harder — re-read the source code for new angles, try combining previous near-misses, try more radical changes. The loop runs until the human interrupts you, period.

As a guide: each experiment takes ~3 minutes, so you can run ~20/hour or ~160 overnight. Make them count.
