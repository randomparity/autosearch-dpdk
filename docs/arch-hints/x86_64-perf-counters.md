# x86_64 `perf` Performance Counter Reference

## Intel (Sapphire/Emerald Rapids) & AMD (Zen 4/Zen 5) PMU Event Guide for Linux

Companion document to the x86_64 optimization checklist. This reference explains what each performance counter measures, highlights the most important events for optimization work, and notes differences between Intel and AMD.

---

## PMU Architecture Overview

x86_64 processors implement a Performance Monitoring Unit (PMU) exposed through Model-Specific Registers (MSRs). Intel defines an Architectural Performance Monitoring interface (version discoverable via `CPUID.0AH`), while AMD provides a compatible but distinct PMU with Instruction-Based Sampling (IBS) extensions.

| Resource | Intel SPR/EMR (PMU v5) | AMD Zen 4 | AMD Zen 5 |
|---|---|---|---|
| Fixed-function counters | 4 (instructions, core cycles, ref cycles, topdown slots) | 3 (instructions, APERF, MPERF) | 3 (instructions, APERF, MPERF) |
| Programmable general-purpose counters | 8 per thread | 6 per thread | 6 per thread |
| Topdown metrics (hardware) | Yes (L1+L2 in PERF_METRICS MSR) | No (software-computed via events) | No (software-computed via events) |
| PEBS (Precise Event Based Sampling) | Yes (enhanced, with PDIST) | No | No |
| IBS (Instruction Based Sampling) | No | Yes (IBS Fetch + IBS Op) | Yes (IBS Fetch + IBS Op, load latency filtering) |
| OFFCORE_RESPONSE MSRs | 2 (MSR 0x1A6, 0x1A7) | N/A (use L3 PMC events) | N/A |
| Hyper-Threading counter sharing | Fixed counters per logical core; programmable counters shared per physical core | N/A (no SMT sharing of PMCs) | N/A |
| Last Branch Record (LBR) | 32 entries | 16 entries | 16 entries |

**Practical constraint**: On Intel SPR/EMR you can count up to 12 events simultaneously (4 fixed + 8 programmable) without multiplexing. On AMD Zen 4/5 you get 9 (3 fixed + 6 programmable). Exceeding these limits causes the kernel to multiplex and scale counts, introducing estimation error. Keep groups within these limits for precise work.

---

## Quick Start: The Essential Five

If you only run one `perf stat` command, make it this one. These five metrics immediately tell you where to focus.

```bash
perf stat -e cycles,instructions,cache-misses,branch-misses,dTLB-load-misses \
    -- ./your_application
```

| Metric | What it tells you | Healthy range |
|---|---|---|
| **IPC** (instructions / cycles) | Overall pipeline efficiency | > 1.5 is good on modern x86; < 0.5 means stalls dominate |
| **cache-misses** | Last-level cache miss rate | < 5% of `cache-references` |
| **branch-misses** | Branch misprediction rate | < 1-2% of `branch-instructions` |
| **dTLB-load-misses** | Data TLB miss rate | Workload-dependent; high = page size or working set problem |

---

## Generic Events → x86 PMU Mapping

Linux `perf` maps generic event names to architecture-specific PMU event codes. The mapping differs between Intel and AMD.

| Generic `perf` event | Intel PMU event (SPR/EMR) | AMD PMU event (Zen 4/5) | Notes |
|---|---|---|---|
| `cpu-cycles` / `cycles` | `CPU_CLK_UNHALTED.THREAD` (fixed ctr 1) | `cpu_clk_unhalted.core` / APERF | Intel counts core clocks; AMD APERF tracks actual frequency |
| `instructions` | `INST_RETIRED.ANY` (fixed ctr 0) | `retired_instructions` / INST_RETIRED_ANY | Both use a fixed counter |
| `cache-references` | `LONGEST_LAT_CACHE.REFERENCE` (event 0x2E, umask 0x4F) | `l3_cache_accesses` | Intel = LLC references; AMD = L3 accesses |
| `cache-misses` | `LONGEST_LAT_CACHE.MISS` (event 0x2E, umask 0x41) | `l3_cache_misses` | Intel = LLC misses; AMD = L3 misses |
| `branch-instructions` | `BR_INST_RETIRED.ALL_BRANCHES` (event 0xC4, umask 0x00) | `retired_branch_instructions` | Architecturally retired branches |
| `branch-misses` | `BR_MISP_RETIRED.ALL_BRANCHES` (event 0xC5, umask 0x00) | `retired_branch_instr_misp` | Mispredicted retired branches |
| `ref-cycles` | `CPU_CLK_UNHALTED.REF_TSC` (fixed ctr 2) | MPERF | Intel = TSC-rate reference clock; AMD MPERF = max P-state clock |

**Key difference from POWER**: On x86, `cycles` and `instructions` use dedicated fixed-function counters that do not consume a programmable counter slot. Both Intel and AMD provide this. On POWER, the equivalent PMC5/PMC6 are also fixed but the event codes changed between P9 and P10.

---

## Detailed Event Reference by Category

### ★ Critical — Pipeline & Instruction Flow

These events form the backbone of all performance analysis. Start here.

| Event Name | Intel (SPR/EMR) | AMD (Zen 4/5) | What it measures |
|---|---|---|---|
| **`INST_RETIRED.ANY`** | Fixed ctr 0 | `retired_instructions` | X86 instructions retired (architecturally completed). Counts through interrupts and traps. |
| **`CPU_CLK_UNHALTED.THREAD`** | Fixed ctr 1 | `cpu_clk_unhalted.core` | Core cycles while not halted. Frequency varies with P-states and turbo. |
| **`CPU_CLK_UNHALTED.REF_TSC`** | Fixed ctr 2 | MPERF | Reference cycles at fixed TSC rate (Intel) or max P-state rate (AMD). Use to measure actual wall time independent of frequency scaling. |
| **`UOPS_ISSUED.ANY`** | Event 0x0E, umask 0x01 | `de_ops_dispatch.all` | Micro-ops issued to the execution engine. Compare with `INST_RETIRED.ANY` to see instruction cracking overhead (complex instructions decode to multiple uops). |
| **`UOPS_RETIRED.SLOTS`** | Event 0xC2, umask 0x02 | N/A (use `retired_uops`) | Retired uops occupying issue slots. On Intel this is the numerator for the TMA "Retiring" metric. |
| **`TOPDOWN.SLOTS`** | Fixed ctr 3 (Intel only) | N/A | Total pipeline slots available. On SPR/EMR this is a fixed counter (slots = 6 per cycle on P-core). Denominator for all TMA Level 1 metrics. |

**How to use**: Compute IPC = `INST_RETIRED.ANY` / `CPU_CLK_UNHALTED.THREAD`. An IPC below 1.0 on modern x86 suggests significant stalls. IPC above 3.0 indicates efficient vectorized or throughput-oriented code.

```bash
# Basic pipeline health
perf stat -e cpu-cycles,instructions,uops_issued.any,uops_retired.slots -- ./app
```

---

### ★ Critical — Stall Cycle Analysis (Intel Top-Down Microarchitecture Analysis)

Intel's TMA method classifies every pipeline slot into one of four categories at Level 1, then drills down at Level 2+. This is the most structured approach to identifying bottlenecks on x86.

#### TMA Level 1 (available via hardware on SPR/EMR)

| Metric | `perf` event name | What it measures | Healthy range |
|---|---|---|---|
| **Retiring** | `topdown-retiring` | Slots used by operations that eventually retired (useful work). | > 50% is good; 100% is theoretical max |
| **Bad Speculation** | `topdown-bad-spec` | Slots wasted on operations that did not retire (mispredicted branches, machine clears). | < 10% |
| **Frontend Bound** | `topdown-fe-bound` | Slots where the frontend (fetch/decode) could not supply uops. Indicates I-cache misses, decode bottlenecks, or branch mispredict recovery. | < 15% |
| **Backend Bound** | `topdown-be-bound` | Slots where the backend could not accept uops. Indicates execution-port pressure, cache misses, or memory latency. | < 30% |

```bash
# TMA Level 1 (requires Intel SPR/EMR or later, or Ice Lake+ client)
perf stat -M TopdownL1 -- ./app

# Or directly:
perf stat -e '{slots,topdown-retiring,topdown-bad-spec,topdown-fe-bound,topdown-be-bound}' -- ./app
```

#### TMA Level 2 (SPR/EMR expose 8 L2 metrics in hardware)

| L1 Category | L2 Sub-metric | What it measures |
|---|---|---|
| Frontend Bound | **Fetch Latency** | Frontend stalled waiting for I-cache, ITLB, or branch resolution |
| Frontend Bound | **Fetch Bandwidth** | Frontend delivering uops, but not enough per cycle |
| Bad Speculation | **Branch Mispredicts** | Slots wasted due to branch mispredictions |
| Bad Speculation | **Machine Clears** | Slots wasted due to pipeline flushes (memory ordering, FP assists) |
| Backend Bound | **Memory Bound** | Execution stalled on data cache misses or memory latency |
| Backend Bound | **Core Bound** | Execution stalled on ALU/port contention, divider, etc. |
| Retiring | **Heavy Operations** | Microcode sequencer (complex instructions: divides, gathers, etc.) |
| Retiring | **Light Operations** | Single-uop instructions (the efficient path) |

```bash
# TMA Level 2 (SPR/EMR)
perf stat -M TopdownL2 -- ./app
```

#### AMD Equivalent

AMD does not provide hardware topdown metrics. Instead, use software-computed approximations:

```bash
# AMD: approximate stall analysis
perf stat -e cycles,instructions,cache-misses,branch-misses,\
stalled-cycles-frontend,stalled-cycles-backend -- ./app

# Frontend stall rate = stalled-cycles-frontend / cycles
# Backend stall rate  = stalled-cycles-backend / cycles
```

---

### ★ Critical — Cache Hierarchy

For NIC-driven workloads (DPDK), cache behavior dominates performance. x86 uses 64-byte cache lines (half the size of POWER's 128-byte lines).

| Event Name | Intel (SPR/EMR) | AMD (Zen 4/5) | What it measures |
|---|---|---|---|
| **`MEM_INST_RETIRED.ALL_LOADS`** | Event 0xD0, umask 0x81 | `ls_dispatch.ld_dispatch` | All load instructions retired. Your denominator for miss rates. PEBS-capable on Intel. |
| **`MEM_LOAD_RETIRED.L1_HIT`** | Event 0xD1, umask 0x01 | N/A (compute from total - misses) | Loads satisfied from L1D. |
| **`MEM_LOAD_RETIRED.L1_MISS`** | Event 0xD1, umask 0x08 | `l1_data_cache_misses` (0x064, umask 0x08) | Loads that missed L1D. PEBS-capable on Intel. |
| **`MEM_LOAD_RETIRED.L2_HIT`** | Event 0xD1, umask 0x02 | `l2_cache_hits_from_dc_misses` | Loads satisfied from L2. |
| **`MEM_LOAD_RETIRED.L2_MISS`** | Event 0xD1, umask 0x10 | `l2_cache_misses_from_dc_misses` | Loads that missed L2. |
| **`MEM_LOAD_RETIRED.L3_HIT`** | Event 0xD1, umask 0x04 | `l3_cache_accesses` (approximate) | Loads satisfied from LLC (L3). |
| **`MEM_LOAD_RETIRED.L3_MISS`** | Event 0xD1, umask 0x20 | `l3_cache_misses` | Loads that missed LLC. This is the most expensive data path -- minimizing this is the primary goal for memory-bound workloads. |
| **`L1-ICACHE-LOAD-MISSES`** | `ICACHE_DATA.STALLS` (event 0x80, umask 0x04) | `ic_fetch_miss_is_any` | Instruction cache misses. High values mean the code footprint exceeds L1I (32-48 KB). |
| **`L2_RQSTS.ALL_DEMAND_DATA_RD`** | Event 0x24, umask 0xE1 | N/A | All demand data read requests to L2. |
| **`L2_RQSTS.DEMAND_DATA_RD_MISS`** | Event 0x24, umask 0x21 | N/A | Demand data read requests that missed L2. |

**How to use -- cache hierarchy efficiency**:
```bash
# Intel: full cache picture
perf stat -e mem_load_retired.l1_miss,mem_load_retired.l2_miss,\
mem_load_retired.l3_miss,mem_load_retired.l3_hit -- ./app

# AMD: cache hierarchy
perf stat -e l1_data_cache_misses,l2_cache_misses_from_dc_misses,\
l3_cache_accesses,l3_cache_misses -- ./app

# L1D miss rate = MEM_LOAD_RETIRED.L1_MISS / MEM_INST_RETIRED.ALL_LOADS
# L3 miss rate  = MEM_LOAD_RETIRED.L3_MISS / MEM_LOAD_RETIRED.L2_MISS
```

#### Intel DDIO Considerations for NIC Workloads

Intel Data Direct I/O (DDIO) allows PCIe devices (NICs) to read/write directly to the LLC, bypassing DRAM. This is critical for DPDK packet processing. DDIO efficiency depends on the LLC not being polluted by application data competing with NIC DMA buffers.

Key uncore events for monitoring DDIO (requires `perf` uncore PMU support):

| Event | Unit | What it measures |
|---|---|---|
| `UNC_CHA_TOR_INSERTS.IO_HIT` | CHA | I/O (PCIe/NIC) requests that hit the LLC -- DDIO working efficiently |
| `UNC_CHA_TOR_INSERTS.IO_MISS` | CHA | I/O requests that missed LLC -- DDIO failing, falling back to DRAM |
| `UNC_CHA_TOR_OCCUPANCY.IO_HIT` | CHA | Cycles * entries for I/O LLC hits -- measures LLC pressure from I/O |

```bash
# DDIO hit rate (system-wide, requires root)
perf stat -a -e 'uncore_cha/event=0x35,umask=0x04,config1=0x00C8F3FF94/' \
              -e 'uncore_cha/event=0x35,umask=0x04,config1=0x00C8F3FF90/' \
    -- sleep 5
# DDIO efficiency = IO_HIT / (IO_HIT + IO_MISS)  (target: > 90%)
```

**AMD note**: AMD does not have a DDIO equivalent. PCIe DMA goes to DRAM, and the CPU fetches from cache hierarchy normally. This simplifies monitoring but means AMD systems rely more on prefetching for NIC workloads.

---

### ★ Critical — TLB & Address Translation

| Event Name | Intel (SPR/EMR) | AMD (Zen 4/5) | What it measures |
|---|---|---|---|
| **`DTLB_LOAD_MISSES.MISS_CAUSES_A_WALK`** | Event 0x08, umask 0x01 | `ls_l1_d_tlb_miss.all` | Data TLB miss that triggered a hardware page table walk. Each walk costs ~7-30 cycles depending on page table depth. |
| **`DTLB_LOAD_MISSES.WALK_COMPLETED_4K`** | Event 0x08, umask 0x02 | N/A | Page walk completed for a 4 KB page. |
| **`DTLB_LOAD_MISSES.WALK_COMPLETED_2M_4M`** | Event 0x08, umask 0x04 | N/A | Page walk completed for a 2 MB or 4 MB huge page. |
| **`DTLB_LOAD_MISSES.WALK_COMPLETED_1G`** | Event 0x08, umask 0x08 | N/A | Page walk completed for a 1 GB huge page. |
| **`DTLB_LOAD_MISSES.WALK_ACTIVE`** | Event 0x08, umask 0x10 | N/A | Cycles when at least one page walk is active (load side). Measures total TLB miss penalty. |
| **`DTLB_STORE_MISSES.MISS_CAUSES_A_WALK`** | Event 0x49, umask 0x01 | N/A | Store-side DTLB miss causing a page walk. |
| **`ITLB_MISSES.MISS_CAUSES_A_WALK`** | Event 0x85, umask 0x01 | `bp_l1_tlb_miss_l2_tlb_miss` | Instruction TLB miss. High count means the code footprint spans too many pages. |

**Page-size-specific TLB events are an Intel strength.** The per-page-size walk completion events let you see exactly which page sizes are causing TLB pressure. AMD provides aggregate TLB miss counts but less per-page-size granularity.

```bash
# Intel: TLB breakdown by page size
perf stat -e dtlb_load_misses.miss_causes_a_walk,\
dtlb_load_misses.walk_completed_4k,\
dtlb_load_misses.walk_completed_2m_4m,\
dtlb_load_misses.walk_completed_1g,\
dtlb_load_misses.walk_active -- ./app

# AMD: aggregate TLB check
perf stat -e ls_l1_d_tlb_miss.all,bp_l1_tlb_miss_l2_tlb_miss,\
cycles,instructions -- ./app
```

---

### Important — Branch Prediction

| Event Name | Intel (SPR/EMR) | AMD (Zen 4/5) | What it measures |
|---|---|---|---|
| **`BR_INST_RETIRED.ALL_BRANCHES`** | Event 0xC4, umask 0x00 | `retired_branch_instructions` | All retired branches. Your denominator for misprediction rate. |
| **`BR_MISP_RETIRED.ALL_BRANCHES`** | Event 0xC5, umask 0x00 | `retired_branch_instr_misp` | Mispredicted retired branches. Each costs ~15-20 cycles on modern x86 (full pipeline flush). |
| **`BR_INST_RETIRED.COND`** | Event 0xC4, umask 0x11 | N/A | Conditional branches retired. |
| **`BR_MISP_RETIRED.COND`** | Event 0xC5, umask 0x11 | N/A | Mispredicted conditional branches. |
| **`BR_INST_RETIRED.NEAR_CALL`** | Event 0xC4, umask 0x02 | N/A | Direct and indirect near call branches. |
| **`BR_INST_RETIRED.INDIRECT`** | Event 0xC4, umask 0x80 | N/A | Indirect branches (virtual calls, switch/jump tables). These are the hardest for the predictor. |
| **`BACLEARS.ANY`** | Event 0xE6, umask 0x01 | N/A | Frontend resteer due to Branch Address Calculator clear. Indicates the initial prediction (from the BPU) was wrong and the BAC corrected it. |

```bash
# Branch prediction efficiency
perf stat -e branch-instructions,branch-misses,\
br_inst_retired.cond,br_misp_retired.cond -- ./app
# Misprediction rate = BR_MISP_RETIRED.ALL_BRANCHES / BR_INST_RETIRED.ALL_BRANCHES
# Target: < 1-2%
```

---

### Important — Memory Subsystem (OFFCORE_RESPONSE & NUMA)

Intel's `OFFCORE_RESPONSE` events provide the most detailed memory hierarchy attribution. They use two programmable MSRs (0x1A6 and 0x1A7) to specify both the request type and the response source.

| Event | Intel | What it measures |
|---|---|---|
| `OFFCORE_RESPONSE.DEMAND_DATA_RD.L3_HIT.SNOOP_HITM` | MSR 0x1A6 | Demand data read hit L3 but another core had a modified copy (cross-core transfer). Indicates true sharing or false sharing. |
| `OFFCORE_RESPONSE.DEMAND_DATA_RD.L3_MISS.LOCAL_DRAM` | MSR 0x1A6 | Demand data read missed L3 and was satisfied from local NUMA node DRAM. |
| `OFFCORE_RESPONSE.DEMAND_DATA_RD.L3_MISS.REMOTE_DRAM` | MSR 0x1A6 | Demand data read missed L3 and was satisfied from remote NUMA node DRAM. This is the most expensive path (~100+ ns). |

```bash
# NUMA locality check (Intel)
# Use perf's named offcore events when available:
perf stat -e mem_load_retired.l3_miss,mem_load_retired.local_pmm,\
offcore_response.demand_data_rd.l3_miss.remote_dram -- ./app

# Or use ocperf.py / perf with raw encoding:
perf stat -e cpu/event=0xb7,umask=0x01,offcore_rsp=0x3FBFC00004/ -- ./app
# (Exact encoding depends on microarchitecture; consult perfmon-events.intel.com)
```

**AMD equivalent**: AMD Zen 4/5 track NUMA via L3 miss events and Data Fabric counters:

```bash
# AMD: check for remote DRAM traffic
perf stat -e l3_cache_misses,ls_dmnd_fills_from_sys.dram_io_near,\
ls_dmnd_fills_from_sys.dram_io_far -- ./app
```

---

### Important — Floating Point & SIMD/AVX

| Event Name | Intel (SPR/EMR) | AMD (Zen 4/5) | What it measures |
|---|---|---|---|
| **`FP_ARITH_INST_RETIRED.SCALAR_DOUBLE`** | Event 0xC7, umask 0x01 | `retired_sse_avx_flops.sp_*` / `retired_sse_avx_flops.dp_*` | Scalar double-precision FP ops retired. |
| **`FP_ARITH_INST_RETIRED.SCALAR_SINGLE`** | Event 0xC7, umask 0x02 | (see above) | Scalar single-precision FP ops retired. |
| **`FP_ARITH_INST_RETIRED.128B_PACKED_DOUBLE`** | Event 0xC7, umask 0x04 | (see above) | 128-bit packed (SSE) double-precision ops. |
| **`FP_ARITH_INST_RETIRED.256B_PACKED_DOUBLE`** | Event 0xC7, umask 0x10 | (see above) | 256-bit packed (AVX) double-precision ops. |
| **`FP_ARITH_INST_RETIRED.512B_PACKED_DOUBLE`** | Event 0xC7, umask 0x40 | N/A (no AVX-512 on Zen 4 desktop; EPYC Zen 5 adds AVX-512) | 512-bit packed (AVX-512) double-precision ops. |
| **`ASSISTS.FP`** | Event 0xC1, umask 0x02 | N/A | FP assists (denormals, overflows). Each assist costs ~150 cycles. If non-zero, investigate denormal inputs. |

**AVX-512 frequency throttling (Intel)**: On Intel SPR, heavy AVX-512 usage triggers a core frequency reduction (License Level 2). The core drops from its base turbo frequency to a lower AVX-512 turbo. This can be ~10-20% lower. Monitor with:

```bash
# Check if AVX-512 is causing frequency throttling
perf stat -e cpu-cycles,ref-cycles,\
fp_arith_inst_retired.512b_packed_double,\
fp_arith_inst_retired.256b_packed_double -- ./app
# If actual frequency (cycles/ref-cycles * TSC_freq) drops during AVX-512 sections,
# consider whether the throughput gain outweighs the frequency penalty.
```

**AMD Zen 4**: No AVX-512 on desktop Zen 4 (Ryzen 7000); EPYC Genoa supports AVX-512 at full width without frequency throttling. **Zen 5**: Full AVX-512 support without frequency penalty.

---

## Using `perf c2c` for False Sharing Detection

`perf c2c` identifies cache lines bouncing between cores -- the hallmark of false sharing. On x86, cache lines are 64 bytes.

```bash
# Record cache-to-cache transfer events (needs root or perf_event_paranoid <= 0)
perf c2c record -a -- sleep 10    # system-wide, or:
perf c2c record -- ./app           # per-process

# Analyze
perf c2c report --stdio

# Look for:
#   "Shared Data Cache Line Table" -- sorted by cache line address
#   High "HITM" counts = cache line modified by one core, read by another
#   "Lcl" vs "Rmt" HITM = same-socket vs cross-socket bouncing
```

**What to look for**: The `HITM` (Hit Modified) column shows cache lines experiencing coherency traffic. On x86, two independent variables within the same 64-byte region will false-share. Common culprits in DPDK: per-lcore statistics structs, ring producer/consumer indexes, mbuf pools.

**Intel vs AMD**: Intel `perf c2c` uses PEBS `MEM_LOAD_RETIRED` events for precise attribution. AMD uses IBS Op sampling, which provides similar data source and address information. Both work with `perf c2c` on recent kernels (5.10+).

```bash
# DPDK-specific: check for false sharing in per-lcore data
perf c2c record -g -- dpdk-testpmd -l 4-11 -- -i
perf c2c report --stdio --sort=cacheline,pid,iaddr
```

---

## Using PEBS for Latency Profiling (Intel)

Intel Precise Event Based Sampling (PEBS) captures the instruction address, data address, load latency, and data source for sampled memory accesses. On SPR/EMR, PEBS supports the PDIST (Precise Distribution) capability for even more accurate IP attribution.

```bash
# Record memory load latency (Intel, PEBS)
perf mem record -t load -- ./app
perf mem report --sort=mem,sym

# Or use the raw event with latency threshold:
perf record -e 'cpu/mem-loads,ldlat=30/P' -- ./app
perf report --sort=mem --stdio

# The ldlat=30 filter captures only loads with >= 30 cycle latency,
# focusing on cache misses and memory accesses.
```

**PEBS data source decoding**: Each PEBS sample includes a data source field that tells you exactly where the data came from (L1, L2, L3, local DRAM, remote DRAM, etc.). Use `perf mem report` to see this breakdown.

### AMD IBS (Instruction Based Sampling)

AMD IBS is conceptually similar to PEBS but uses a different mechanism. IBS has two independent sampling units:

| IBS Unit | PMU name in `perf` | What it captures |
|---|---|---|
| **IBS Fetch** | `ibs_fetch` | Instruction fetch events: ITLB hit/miss, IC hit/miss, fetch latency, page size |
| **IBS Op** | `ibs_op` | Execution events: load/store address, data source (L1/L2/L3/DRAM), DTLB info, branch taken/mispredicted, tagged latency |

```bash
# AMD IBS: memory access profiling
perf record -e ibs_op/cnt_ctl=1,l3missonly=0/ -- ./app
perf mem report --sort=mem,sym

# AMD IBS: load latency profiling (Zen 5, with ldlat filtering)
perf mem record -e ibs_op// -t load --ldlat 30 -- ./app
perf mem report --stdio

# AMD IBS: raw dump for detailed analysis
perf record -e ibs_op// -- ./app
perf script -F ip,addr,phys_addr,data_src,weight
```

**Zen 5 enhancement**: Zen 5 adds hardware load latency filtering to IBS Op, matching Intel PEBS `ldlat` capability. On Zen 4, all IBS Op samples are captured regardless of latency, and filtering must be done in post-processing.

---

## Recommended Analysis Workflows

### Workflow 1: Top-Down Performance Triage

```bash
# Step 1: Overall health check
perf stat -d -- ./app
# -d adds L1-dcache-loads/misses, LLC-loads/misses, dTLB-loads/misses, etc.

# Step 2: TMA Level 1 (Intel)
perf stat -M TopdownL1 -- ./app
# Or on AMD:
perf stat -e cycles,instructions,stalled-cycles-frontend,stalled-cycles-backend,\
cache-misses,branch-misses -- ./app

# Step 3: TMA Level 2 (Intel SPR/EMR)
perf stat -M TopdownL2 -- ./app

# Step 4: If cache-bound, drill into cache hierarchy
# Intel:
perf stat -e mem_load_retired.l1_miss,mem_load_retired.l2_miss,\
mem_load_retired.l3_miss,l1-icache-load-misses -- ./app
# AMD:
perf stat -e l1_data_cache_misses,l2_cache_misses_from_dc_misses,\
l3_cache_misses,ic_fetch_miss_is_any -- ./app

# Step 5: If TLB-bound, drill into page sizes (Intel)
perf stat -e dtlb_load_misses.miss_causes_a_walk,\
dtlb_load_misses.walk_completed_4k,\
dtlb_load_misses.walk_completed_2m_4m,\
dtlb_load_misses.walk_completed_1g -- ./app

# Step 6: If contention-bound, run c2c
perf c2c record -- ./app && perf c2c report --stdio
```

### Workflow 2: NUMA Locality (Multi-Socket)

```bash
# Intel: OFFCORE_RESPONSE for local vs remote DRAM
perf stat -e mem_load_retired.l3_miss,\
offcore_response.demand_data_rd.l3_miss.local_dram,\
offcore_response.demand_data_rd.l3_miss.remote_dram -- ./app

# Compare with NUMA pinning:
perf stat -e mem_load_retired.l3_miss -- numactl --cpunodebind=0 --membind=0 ./app
perf stat -e mem_load_retired.l3_miss -- numactl --cpunodebind=0 --membind=1 ./app
# Second run should show dramatically more L3 misses and higher cycle counts
```

### Workflow 3: DDIO Efficiency (NIC Workloads, Intel)

```bash
# Monitor DDIO hit/miss via CHA uncore events (system-wide, root)
perf stat -a -e uncore_cha/event=0x35,umask=0x04,config1=0x00C8F3FF94/,\
uncore_cha/event=0x35,umask=0x04,config1=0x00C8F3FF90/ -- sleep 10
# DDIO hit rate = IO_HIT / (IO_HIT + IO_MISS)

# Or use Intel PCM (Performance Counter Monitor) for a friendlier interface:
pcm --external-program -- ./dpdk-app
```

### Workflow 4: Hot Function Profiling

```bash
# Record with call graph (DWARF unwinding, most reliable on x86)
perf record -g --call-graph dwarf -e cycles -- ./app
perf report

# Or use frame pointers if available (lower overhead):
perf record -g --call-graph fp -e cycles -- ./app

# Annotate assembly of the hot function
perf annotate --symbol=hot_function
# Look for: lock-prefixed instructions, cache-miss-prone loads, unvectorized loops
```

---

## Intel vs AMD Differences

| Topic | Intel (SPR/EMR) | AMD (Zen 4/5) |
|---|---|---|
| Top-Down Analysis | Hardware TMA L1+L2 via `PERF_METRICS` MSR | Software-only via `stalled-cycles-*` events |
| Precise Sampling | PEBS (Precise Event Based Sampling) with PDIST | IBS (Instruction Based Sampling): Fetch + Op units |
| Load Latency Filtering | `ldlat=N` in PEBS | Zen 5: hardware `ldlat`; Zen 4: post-filter only |
| OFFCORE_RESPONSE | 2 programmable MSRs for request+response filtering | N/A; use `ls_dmnd_fills_from_sys.*` events |
| DDIO (NIC -> LLC) | Yes; monitor via CHA uncore events | No equivalent; PCIe DMA goes to DRAM |
| AVX-512 Frequency | Frequency throttling (License Level 2 on SPR) | No throttling (Zen 4 EPYC, Zen 5) |
| LLC Topology | Shared across all cores in a tile/socket | Per-CCX L3 (up to 32 MB per CCX, 8 cores) |
| Fixed Counters | 4 (inst, cycles, ref-cycles, slots) | 3 (inst, APERF, MPERF) |
| Programmable Counters | 8 per thread | 6 per thread |
| Cache Line Size | 64 bytes | 64 bytes |
| Last Branch Record | 32 entries | 16 entries (Zen 4/5) |
| Uncore Monitoring | Extensive (CHA, IMC, UPI, IIO, PCU) | Data Fabric, UMC, L3 |

---

## Discovering Available Events on Your System

```bash
# List all PMU events available on this specific CPU
perf list

# List with full descriptions (Intel vendor events)
perf list --long-desc

# Show events by category
perf list cache
perf list tlb

# Show raw event encoding for a named event
perf stat -e mem_load_retired.l3_miss -v -- true 2>&1 | grep config

# List available metric groups (Intel TMA, etc.)
perf list metric
perf list metricgroup

# AMD: list IBS events
perf list | grep ibs
```
