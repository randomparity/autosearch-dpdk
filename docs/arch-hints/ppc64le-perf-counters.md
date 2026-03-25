# ppc64le `perf` Performance Counter Reference

## POWER9 / Power10 / Power11 PMU Event Guide for Linux

Companion document to the ppc64le optimization checklist. This reference explains what each performance counter measures, highlights the most important events for optimization work, and notes differences across CPU generations.

---

## PMU Architecture Overview

POWER processors implement a Performance Monitor Unit (PMU) based on the ISA 2.07 / 3.0 / 3.1 specifications. The key hardware resources are:

| Resource | POWER9 | Power10 | Power11 |
|---|---|---|---|
| Programmable PMCs (PMC1–PMC4) | 4 | 4 | 4 |
| Fixed-function PMCs (PMC5, PMC6) | 2 (cycles, instructions) | 2 (cycles, instructions) | 2 (cycles, instructions) |
| MMCR control registers | MMCR0, MMCR1, MMCRA, MMCR2 | MMCR0, MMCR1, MMCRA, MMCR2, **MMCR3** | MMCR0, MMCR1, MMCRA, MMCR2, MMCR3 |
| SIER (Sampled Info Event Register) | SIER | SIER, **SIER2**, **SIER3** | SIER, SIER2, SIER3 |
| BHRB (Branch History Rolling Buffer) | 32 entries | 32 entries | 32 entries |
| Threshold counter support | Yes | Yes (enhanced) | Yes (enhanced) |
| Data source tagging | Yes | Yes (extended encoding) | Yes (extended encoding) |
| Marked instruction sampling | Yes | Yes | Yes |

**Practical constraint**: You can count up to 6 events simultaneously (4 programmable + 2 fixed), but some events are restricted to specific PMCs. If you specify more than 6, the kernel will multiplex and scale the counts — this introduces estimation error, so keep groups ≤ 6 for precise work.

---

## Quick Start: The Essential Five

If you only run one `perf stat` command, make it this one. These five metrics immediately tell you where to focus.

```bash
perf stat -e cycles,instructions,cache-misses,branch-misses,dTLB-load-misses \
    -- ./your_application
```

| Metric | What it tells you | Healthy range |
|---|---|---|
| **IPC** (instructions / cycles) | Overall pipeline efficiency | > 1.0 is good; < 0.5 means stalls dominate |
| **cache-misses** | L1D miss rate (maps to `PM_LD_MISS_L1`) | < 5% of `cache-references` |
| **branch-misses** | Branch misprediction rate (`PM_BR_MPRED_CMPL`) | < 2% of `branch-instructions` |
| **dTLB-load-misses** | TLB miss rate (`PM_DTLB_MISS`) | Workload-dependent; high = page size problem |

---

## Generic Events → POWER PMU Mapping

Linux `perf` maps generic event names to architecture-specific PMU event codes. Here is the mapping for all three generations:

| Generic `perf` event | POWER9 PMU event | Power10/P11 PMU event | Notes |
|---|---|---|---|
| `cpu-cycles` / `cycles` | `PM_CYC` (0x0001e) | `PM_CYC` (0x600f4) / alt 0x0001e | **Event codes differ**. P10/P11 default to `PM_RUN_CYC` semantics on PMC5/6 |
| `instructions` | `PM_INST_CMPL` (0x00002) | `PM_INST_CMPL` (0x500fa) / alt 0x00002 | **Event codes differ**. P10/P11 count on fixed PMC5/6 by default |
| `cache-references` | `PM_LD_REF_L1` (0x100fc) | `PM_LD_REF_L1` (0x100fc) | Same across all generations |
| `cache-misses` | `PM_LD_MISS_L1_FIN` (0x2c04e) | `PM_LD_MISS_L1` (0x3e054) | Different event code |
| `branch-instructions` | `PM_BR_CMPL` (0x4d05e) | `PM_BR_CMPL` (0x4d05e) | Same across all generations |
| `branch-misses` | `PM_BR_MPRED_CMPL` (0x400f6) | `PM_BR_MPRED_CMPL` (0x400f6) | Same across all generations |

**Key difference from x86**: On x86 `cycles` and `instructions` use dedicated fixed-function counters that don't consume a programmable PMC. On POWER, PMC5 and PMC6 are dedicated to these two events, but the event codes assigned to them changed between P9 and P10/P11.

---

## Detailed Event Reference by Category

### ★ Critical — Pipeline & Instruction Flow

These events form the backbone of all performance analysis. Start here.

| Event Name | Code (P9) | Code (P10/P11) | PMC | What it measures |
|---|---|---|---|---|
| **`PM_CYC`** | 0x0001e | 0x0001e (alt) | PMC5 | Processor cycles elapsed (counts even when idle). On P10+, default `cycles` uses `PM_RUN_CYC` on PMC6 instead, which excludes idle cycles. |
| **`PM_RUN_CYC`** | 0x600f4 | 0x600f4 | PMC6/any | Cycles while the run latch is set (i.e., not idle). This is what you typically want for application profiling since it excludes OS idle time. |
| **`PM_INST_CMPL`** | 0x00002 | 0x00002 (alt) | PMC5 | PowerPC instructions completed. Counts every architecturally completed instruction. |
| **`PM_RUN_INST_CMPL`** | 0x500fa | 0x500fa | PMC5/any | Instructions completed while run latch is set. Preferred for IPC calculations — pairs with `PM_RUN_CYC`. |
| **`PM_INST_DISP`** | 0x200f2 | 0x200f2 | any | Instructions dispatched into the execution pipeline. Compare with `PM_INST_CMPL` to see how much work is being thrown away (speculation, flushes). |
| **`PM_FLOP_CMPL`** | 0x100f4 | 0x100f4 | any | Floating-point operations completed. Essential for HPC/scientific workloads. Note: on P9, this was on a blacklist for certain DD levels — check your firmware. |

**How to use**: Compute IPC = `PM_RUN_INST_CMPL` / `PM_RUN_CYC`. IPC below 1.0 on POWER9 or below 1.5 on Power10 (which doubled execution units) suggests significant stalls.

```bash
perf stat -e PM_RUN_CYC,PM_RUN_INST_CMPL,PM_INST_DISP,PM_FLOP_CMPL -- ./app
```

---

### ★ Critical — Stall Cycle Analysis

These events tell you *why* the pipeline is stalled. This is the most actionable category.

| Event Name | Code (P9) | Code (P10/P11) | What it measures |
|---|---|---|---|
| **`PM_CMPLU_STALL`** | 0x1e054 | — | (P9 only) Total completion stall cycles. The NPC (next-to-complete) instruction could not complete because it was waiting for something. This is the umbrella stall event. |
| **`PM_DISP_STALL_CYC`** | — | 0x100f8 | (P10/P11 only) Cycles where dispatch was stalled. Replaces the P9 stall model with a new dispatch-centric view. |
| **`PM_EXEC_STALL`** | — | 0x30008 | (P10/P11 only) Cycles where execution was stalled. |
| **`PM_ICT_NOSLOT_CYC`** | 0x100f8 | — | (P9) Cycles where no instruction was available from the ICT (instruction completion table) — typically indicates frontend starvation (icache miss, branch mispredict recovery). |

**Generation difference**: P9 uses a *completion-centric* stall model (`PM_CMPLU_STALL` and its sub-events). P10/P11 switched to a *dispatch/execution-centric* model (`PM_DISP_STALL_CYC`, `PM_EXEC_STALL`), which more directly identifies the pipeline stage causing the bottleneck.

```bash
# POWER9 stall breakdown
perf stat -e PM_CYC,PM_CMPLU_STALL,PM_ICT_NOSLOT_CYC,PM_INST_CMPL -- ./app

# Power10/P11 stall breakdown
perf stat -e PM_CYC,PM_DISP_STALL_CYC,PM_EXEC_STALL,PM_INST_CMPL -- ./app
```

---

### ★ Critical — Cache Hierarchy

For the 128-byte-cache-line optimization work, these events are essential.

| Event Name | Code (P9) | Code (P10/P11) | What it measures |
|---|---|---|---|
| **`PM_LD_REF_L1`** | 0x100fc | 0x100fc | All L1D load references counted at finish, gated by reject. This is your total load count denominator. |
| **`PM_LD_MISS_L1`** | 0x3e054 | 0x3e054 | Loads that missed in L1D. Divide by `PM_LD_REF_L1` for L1D miss rate. Remember: L1D lines are 128 bytes on POWER — twice x86. |
| **`PM_LD_MISS_L1_FIN`** | 0x2c04e | — | (P9) Load missed L1, counted at finish time. Alternate measurement point for the same miss event. |
| **`PM_ST_MISS_L1`** | 0x300f0 | 0x300f0 | Stores that missed in L1D. Store-through on P9 (all stores write through to L2), so this indicates L1D write-allocation misses. |
| **`PM_DATA_FROM_L3`** | 0x4c042 | 0x01300000001c040 | L1D reloaded from the local core's L3 cache due to a demand load. Indicates the data was on-chip but not in L1/L2. |
| **`PM_DATA_FROM_L3MISS`** | 0x300fe | 0x200fe | Demand load satisfied from beyond L3 (memory, remote cache, etc.). This is the most expensive data path and the event to minimize. |
| **`PM_L2_ST`** | 0x16880 | 0x016880 | All successful D-side store dispatches (L2 level). This is an L2 event — note that L2 events on POWER run in a 2:1 clock domain, so raw counts must often be multiplied by 2. |
| **`PM_L2_ST_MISS`** | 0x26880 | 0x26880 | Store dispatches that missed in L2. |
| **`PM_L1_PREF`** | 0x20054 | — | (P9) L1 cache data prefetches. Compare with L1 miss rate to see if the hardware prefetcher is keeping up. |
| **`PM_LD_PREFETCH_CACHE_LINE_MISS`** | — | 0x1002c | (P10/P11) Load prefetch cache-line miss. P10 name for a similar concept. |
| **`PM_L3_PREF_ALL`** | 0x4e052 | — | (P9) Total L3 prefetches (load + store). |
| **`PM_L1_ICACHE_MISS`** | 0x200fd | 0x200fc | Demand instruction cache miss. High values indicate the instruction footprint exceeds L1I (32 KB). |
| **`PM_INST_FROM_L1`** | 0x04080 | 0x04080 | Instructions fetched from L1I. |
| **`PM_INST_FROM_L1MISS`** | — | 0x03f00000001c040 | (P10/P11) Instructions fetched after L1I miss. Extended event code with data source encoding. |

**How to use — cache hierarchy efficiency**:
```bash
# Full cache picture
perf stat -e PM_LD_REF_L1,PM_LD_MISS_L1,PM_ST_MISS_L1,PM_DATA_FROM_L3MISS -- ./app

# L1D miss rate = PM_LD_MISS_L1 / PM_LD_REF_L1
# L3 miss rate  = PM_DATA_FROM_L3MISS / PM_LD_MISS_L1
```

**P10/P11 advantage**: The 4× larger L2 cache (2 MB vs. 512 KB) means that a working set which thrashes L2 on P9 may fit entirely on P10. If `PM_DATA_FROM_L3` is high on P9 but you're planning a P10 migration, expect a significant uplift for free.

---

### ★ Critical — TLB & Address Translation

Direct evidence for page-size tuning decisions.

| Event Name | Code (P9) | Code (P10/P11) | What it measures |
|---|---|---|---|
| **`PM_DTLB_MISS`** | 0x300fc | 0x300fc | Data TLB miss requiring a PTEG reload (page table walk). Each miss costs 20–36 cycles on P9 depending on TLB level. |
| **`PM_ITLB_MISS`** | 0x400fc | 0x400fc | Instruction TLB miss. High count means the code footprint spans too many pages. |
| `PM_DTLB_MISS_4K` | 0x2c056 | — | (P9) DTLB miss for a 4 KB page. |
| `PM_DTLB_MISS_64K` | 0x3c056 | — | (P9) DTLB miss for a 64 KB page. |
| `PM_DTLB_MISS_2M` | 0x1c05c | — | (P9) DTLB miss for a 2 MB page. |
| `PM_DTLB_MISS_16M` | 0x4c056 | — | (P9) DTLB miss for a 16 MB page. |
| `PM_DTLB_MISS_1G` | 0x4c05a | — | (P9) DTLB miss for a 1 GB page. |
| `PM_DTLB_MISS_16G` | 0x1c058 | — | (P9) DTLB miss for a 16 GB page. |
| `PM_DERAT_MISS_2M` | 0x1c05a | — | (P9) D-ERAT miss for 2 MB pages. The ERAT is a first-level TLB cache (64-entry, fully associative on P9). ERAT misses are cheaper than full TLB misses but still cost ~20 cycles. |
| `PM_DERAT_MISS_1G` | 0x2c05a | — | (P9) D-ERAT miss for 1 GB pages. |
| `PM_RADIX_PWC_L1_HIT` | 0x1f056 | — | (P9, Radix MMU) Page walk cache L1 hit. Indicates the Radix page table walker found the translation in its L1 cache — fast path. |
| `PM_RADIX_PWC_L2_HIT` | 0x2d024 | — | (P9) Page walk cache L2 hit. |
| `PM_RADIX_PWC_L3_HIT` | 0x3f056 | — | (P9) Page walk cache L3 hit. |

**Page-size-specific TLB events are a POWER9 specialty.** These per-page-size breakdowns let you see exactly which page sizes are causing TLB pressure. On P10/P11, the TLB expanded from 1024 to 4096 entries with lower latency, so you may see dramatically fewer misses with the same workload.

```bash
# P9: Full TLB breakdown by page size
perf stat -e PM_DTLB_MISS,PM_DTLB_MISS_4K,PM_DTLB_MISS_64K,PM_DTLB_MISS_16M,PM_ITLB_MISS -- ./app

# P10/P11: Aggregate TLB check
perf stat -e PM_DTLB_MISS,PM_ITLB_MISS,PM_RUN_CYC,PM_RUN_INST_CMPL -- ./app
```

---

### Important — Branch Prediction

| Event Name | Code (all) | What it measures |
|---|---|---|
| **`PM_BR_CMPL`** | 0x4d05e | All branches completed. Your denominator for misprediction rate. |
| **`PM_BR_MPRED_CMPL`** | 0x400f6 | Mispredicted branches completed. Each misprediction flushes the pipeline, costing ~15–20 cycles on P9 and ~12–15 on P10 (improved branch predictor). |
| `PM_BR_2PATH` | 0x20036 | (P9) Branches that are not strongly biased — the predictor had low confidence. High count means the branch predictor is struggling. |
| `PM_BR_FIN` | — / 0x2f04a | (P10/P11) Branch finished. |
| `PM_BR_TKN_UNCOND_FIN` | — / 0x48B4 | (P10/P11) Unconditional branch finished (always taken). Not useful for prediction analysis but useful for understanding control flow. |

**P10/P11 improvement**: Power10 doubled branch predictor accuracy vs P9. If you see high misprediction rates on P9, re-benchmark on P10 before investing in branch layout optimization.

```bash
# Branch prediction efficiency
perf stat -e PM_BR_CMPL,PM_BR_MPRED_CMPL,PM_RUN_CYC -- ./app
# Misprediction rate = PM_BR_MPRED_CMPL / PM_BR_CMPL  (target: < 2%)
```

---

### Important — Memory Subsystem (Data Source)

These tell you *where* cache reloads came from. Extended event codes on P10/P11 use the Data Source field in SIER for precise attribution.

| Event Name | Generation | What it measures |
|---|---|---|
| `PM_DATA_FROM_L3` | P9, P10 | Reload from local core's L3. |
| `PM_DATA_FROM_L3MISS` | P9, P10 | Reload from beyond L3 (DRAM, remote cache). |
| `PM_DATA_FROM_LMEM` | P10 | Reload from local chip's memory (DRAM). |
| `PM_DATA_FROM_RMEM` | P10 | Reload from remote chip's memory (cross-socket NUMA). **This is the most expensive reload path** — if this is high, you have a NUMA locality problem. |
| `PM_DATA_FROM_L3_NO_CONFLICT` | P10 | L3 hit with no conflict — clean, fast L3 access. |
| `PM_DATA_FROM_L3_CONFLICT` | P10 | L3 hit with conflict — another core was using the line. Indicates sharing/contention. |

```bash
# Where is data coming from? (Power10)
perf stat -e PM_LD_REF_L1,PM_LD_MISS_L1,PM_DATA_FROM_L3,PM_DATA_FROM_L3MISS,PM_DATA_FROM_LMEM -- ./app
```

---

### Important — Floating Point & Vector/VSX

| Event Name | Code | Generation | What it measures |
|---|---|---|---|
| `PM_FLOP_CMPL` | 0x100f4 | P9, P10, P11 | Floating-point operations completed. For FLOPS rate = `PM_FLOP_CMPL` / wall_time. |
| `PM_VECTOR_LD_CMPL` | 0x44054 | P10, P11 | Vector load instructions completed. Indicates VSX/AltiVec vector register load activity. |
| `PM_VECTOR_ST_CMPL` | 0x44056 | P10, P11 | Vector store instructions completed. |
| `PM_MMA_ISSUED` | (varies) | P10, P11 | MMA instruction issued. **Power10/P11 only** — tracks use of the Matrix Math Accelerator. If this is zero for a workload that does matrix math, your BLAS library is not using MMA. |

```bash
# Check MMA utilization (Power10/P11)
perf stat -e PM_FLOP_CMPL,PM_MMA_ISSUED,PM_VECTOR_LD_CMPL,PM_VECTOR_ST_CMPL,PM_RUN_CYC -- ./app
# If PM_MMA_ISSUED = 0 and PM_FLOP_CMPL is high, you're leaving MMA perf on the table
```

---

### Important — SMT & Thread Contention

| Event Name | Code | Generation | What it measures |
|---|---|---|---|
| `PM_RUN_CYC_SMT2_MODE` | 0x3006c | P9 | Cycles running in SMT2 mode. Compare with `PM_RUN_CYC` to see what fraction of time the core was sharing resources with other threads. |
| `PM_RUN_INST_CMPL_CONC` | 0x300F4 | P10, P11 | Instructions completed when all threads in the core had run latch set. Indicates how much work happens during full-SMT periods. |

**Practical SMT analysis**: Run your application at SMT1 vs SMT4 vs SMT8 and compare total throughput. Per-thread IPC will drop as SMT depth increases (shared L1, shared dispatch bandwidth), but total core throughput should increase for throughput-oriented workloads.

```bash
# Compare at different SMT levels
ppc64_cpu --smt=1 && perf stat -e PM_RUN_CYC,PM_RUN_INST_CMPL -- ./app
ppc64_cpu --smt=4 && perf stat -e PM_RUN_CYC,PM_RUN_INST_CMPL -- ./app
```

---

### Specialized — Store Alignment & Atomics

| Event Name | Code | Generation | What it measures |
|---|---|---|---|
| `PM_ST0_UNALIGNED_FIN` | 0xC0A4 | P10 | Store instructions on port 0 that crossed the 128-byte boundary, requiring an extra pipeline cycle. **Adds ~10 cycles of latency.** Direct evidence that struct layout or buffer alignment needs fixing. |
| `PM_ST1_UNALIGNED_FIN` | 0xC8A4 | P10 | Same for store port 1. |
| `PM_STCX_SUCCESS_CMPL` | 0xC8B8 | P10 | Successful store-conditional (compare-and-swap) instructions. Low success rate indicates high contention on atomic variables. |

```bash
# Unaligned store penalty (Power10)
perf stat -e PM_ST0_UNALIGNED_FIN,PM_ST1_UNALIGNED_FIN,PM_ST_MISS_L1 -- ./app
```

---

### Specialized — TLBIE Snooping (P10/P11)

These events are relevant for systems with heavy TLB invalidation traffic (containers, VMs, heavy mmap/munmap workloads).

| Event Name | Code | What it measures |
|---|---|---|
| `PM_SNOOP_TLBIE_WAIT_ST_CYC` | 0xF884 | Cycles a TLBIE snoop was delayed waiting for older stores to drain. |
| `PM_SNOOP_TLBIE_WAIT_LD_CYC` | 0xF088 | Cycles a TLBIE snoop was delayed waiting for older loads to drain. |
| `PM_SNOOP_TLBIE_WAIT_MMU_CYC` | 0xF08C | Cycles a TLBIE snoop was delayed waiting for the MMU to finish invalidation. |

---

### Specialized — Frontend Starvation (P10/P11)

| Event Name | Code | What it measures |
|---|---|---|
| `PM_NO_FETCH_IBUF_FULL_CYC` | 0x4884 | Cycles where no instructions were fetched because the instruction buffer was full. Indicates backend pressure causing frontend backup. |
| `PM_ISSUE_KILL` | (frontend.json) | Cycles where an issued instruction group was cancelled. High count indicates heavy speculation or pipeline flushes. |
| `PM_IC_PREF_REQ` | 0x040a0 | (P10/P11) Instruction cache prefetch requests. |

---

### Specialized — Interrupt & External Event Overhead

| Event Name | Code | Generation | What it measures |
|---|---|---|---|
| `PM_EXT_INT` | 0x200F8 | P10, P11 | Cycles an external interrupt was active. High values indicate interrupt storms or misconfigured interrupt affinity. |

---

## Using `perf c2c` for False Sharing Detection

This is the single most important tool for the 128-byte cache line work. `perf c2c` identifies cache lines that are being bounced between cores — the hallmark of false sharing.

```bash
# Record cache-to-cache transfer events (needs root)
perf c2c record -a -- sleep 10    # system-wide, or:
perf c2c record -- ./app           # per-process

# Analyze
perf c2c report --stdio

# Look for:
#   "Shared Data Cache Line Table" — sorted by cache line address
#   High "HITM" counts = cache line modified by one core, read by another
#   Remember: on POWER, a "cache line" is 128 bytes
```

**What to look for**: The `HITM` (Hit Modified) column shows cache lines experiencing coherency traffic. On POWER, any two independent variables within the same 128-byte region will false-share. This is twice as likely as on x86 (64-byte lines), making `perf c2c` doubly important.

---

## Using Marked Instructions for Latency Profiling

POWER PMU supports "marked instruction" sampling via `PM_MRK_INST_CMPL` (0x401e0). When a marked instruction completes, the PMU captures detailed information in SIER, including:

- Data source (where the cache reload came from)
- Latency (cycles from dispatch to completion)
- Address (effective address of the load/store)

```bash
# Memory access profiling (captures load latency distribution)
perf record -e '{PM_MRK_INST_CMPL}:p' --weight -- ./app
perf report --sort=mem --stdio

# P9 convenience events
perf record -e MEM_LOADS -- ./app    # raw: 0x34340401e0
perf record -e MEM_STORES -- ./app   # raw: 0x343c0401e0
```

These events use the Sampling Mode, Eligibility, and Threshold fields in MMCRA to tag random instructions with latency data. On **P10/P11**, the additional SIER2/SIER3 registers and MMCR3 provide extended filtering capabilities for more precise sampling.

---

## L2 Event Counting Caveat

**This is a common gotcha.** L2 cache events on POWER (e.g., `PM_L2_ST`, `PM_L2_ST_MISS`, `PM_L2_INST_MISS`) operate in a **2:1 clock domain** and are **time-sliced across all 4 threads** in an SMT4 core. The reported raw count must often be **multiplied by 2** to get the true event count. Check the event description (`perf list --desc`) for each L2 event to see if this adjustment applies.

```bash
# List all available events with descriptions
perf list --long-desc | grep -A2 "PM_L2"
```

---

## Recommended Analysis Workflows

### Workflow 1: Top-Down Performance Triage

```bash
# Step 1: Overall health check
perf stat -d -- ./app
# -d adds L1-dcache-loads/misses, LLC-loads/misses, dTLB-loads/misses, etc.

# Step 2: Stall breakdown (P9)
perf stat -e PM_RUN_CYC,PM_RUN_INST_CMPL,PM_CMPLU_STALL,PM_ICT_NOSLOT_CYC,PM_BR_MPRED_CMPL,PM_LD_MISS_L1 -- ./app

# Step 2: Stall breakdown (P10/P11)
perf stat -e PM_RUN_CYC,PM_RUN_INST_CMPL,PM_DISP_STALL_CYC,PM_EXEC_STALL,PM_BR_MPRED_CMPL,PM_LD_MISS_L1 -- ./app

# Step 3: If cache-bound, drill into cache hierarchy
perf stat -e PM_LD_REF_L1,PM_LD_MISS_L1,PM_ST_MISS_L1,PM_DATA_FROM_L3,PM_DATA_FROM_L3MISS,PM_L1_ICACHE_MISS -- ./app

# Step 4: If TLB-bound, drill into page sizes (P9)
perf stat -e PM_DTLB_MISS,PM_DTLB_MISS_4K,PM_DTLB_MISS_64K,PM_DTLB_MISS_16M,PM_ITLB_MISS -- ./app

# Step 5: If contention-bound, run c2c
perf c2c record -- ./app && perf c2c report --stdio
```

### Workflow 2: MMA Utilization Check (Power10/P11)

```bash
perf stat -e PM_RUN_CYC,PM_RUN_INST_CMPL,PM_FLOP_CMPL,PM_MMA_ISSUED,PM_VECTOR_LD_CMPL,PM_VECTOR_ST_CMPL -- ./app

# Expected: if workload does matrix math, PM_MMA_ISSUED should be >> 0
# If PM_FLOP_CMPL is high but PM_MMA_ISSUED is 0:
#   → Your BLAS library is not MMA-aware
#   → Rebuild OpenBLAS with -mcpu=power10 or switch to IBM ESSL
```

### Workflow 3: NUMA Locality (Multi-Socket)

```bash
# Check for cross-socket memory traffic
perf stat -e PM_DATA_FROM_L3MISS,PM_RUN_CYC -- numactl --cpunodebind=0 --membind=0 ./app
perf stat -e PM_DATA_FROM_L3MISS,PM_RUN_CYC -- numactl --cpunodebind=0 --membind=1 ./app
# Second run should show dramatically more L3 misses and higher cycle counts
```

### Workflow 4: Hot Function Profiling

```bash
# Record with call graph
perf record -g -e PM_RUN_CYC -- ./app
perf report

# Annotate assembly of the hot function
perf annotate --symbol=hot_function
# Look for: lwsync/sync instructions, cache-miss-prone loads, unvectorized loops
```

---

## Discovering Available Events on Your System

```bash
# List all PMU events available on this specific CPU
perf list pmu

# List with full descriptions
perf list --long-desc pmu

# List JSON-defined vendor events (power9, power10, power11)
perf list --long-desc | grep PM_

# Show raw event encoding for a named event
perf stat -e PM_LD_MISS_L1 -v -- true 2>&1 | grep config
```

---

## Cross-Generation Migration Notes

| Topic | POWER9 | Power10 / Power11 |
|---|---|---|
| Stall model | Completion-centric (`PM_CMPLU_STALL`) | Dispatch/execution-centric (`PM_DISP_STALL_CYC`, `PM_EXEC_STALL`) |
| Default `cycles` event | `PM_CYC` (0x0001e) on PMC5 | `PM_CYC` (0x600f4) on PMC6, alt 0x0001e |
| Default `instructions` | `PM_INST_CMPL` (0x00002) on PMC5 | `PM_INST_CMPL` (0x500fa) on PMC5, alt 0x00002 |
| Per-page-size DTLB events | Yes (4K, 64K, 2M, 16M, 1G, 16G) | Not in base event list (use `PM_DTLB_MISS` aggregate) |
| MMA events | Not applicable | `PM_MMA_ISSUED` and related |
| MMCR3 register | Not present | Present — enables extended threshold/filtering |
| SIER2/SIER3 | Not present | Present — richer sampling metadata |
| L2 events clock domain | 2:1, multiply by 2 | 2:1, multiply by 2 |
| BHRB (branch history) | 32 entries, IFM1 mask | 32 entries, IFM1+IFM2+IFM3 masks |
| Data source encoding | Basic | Extended (more granular memory hierarchy attribution) |
