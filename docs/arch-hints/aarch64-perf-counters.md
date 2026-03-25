# aarch64 `perf` Performance Counter Reference

## Neoverse N1 / N2 / V2 / N3 / V3 PMU Event Guide for Linux

Companion document to the aarch64 optimization checklist. This reference explains what each performance counter measures, highlights the most important events for optimization work, and notes differences across Neoverse generations.

---

## PMU Architecture Overview

Arm server cores implement a Performance Monitoring Unit based on the PMUv3 architecture (ARMv8-A). The key hardware resources are:

| Resource | Neoverse N1 (Graviton 2) | Neoverse N2 (Graviton 3E, Yitian 710) | Neoverse V2 (Grace, Graviton 4) |
|---|---|---|---|
| Programmable counters | 6 | 6 | 6 |
| Fixed cycle counter (PMCCNTR_EL0) | 1 (CPU_CYCLES) | 1 (CPU_CYCLES) | 1 (CPU_CYCLES) |
| Pipeline width (dispatch slots) | 4-wide | 5-wide | 5-wide |
| Top-down slot events | No (cycle-based stalls only) | Yes (`STALL_SLOT_FRONTEND`/`STALL_SLOT_BACKEND`) | Yes (`STALL_SLOT_FRONTEND`/`STALL_SLOT_BACKEND`) |
| Statistical Profiling (SPE) | Yes (v8.2) | Yes (v8.2) | Yes (v8.2) |
| SVE support | No | Yes (SVE2, 128-bit) | Yes (SVE2, 128-bit) |
| Branch record (BRBE) | No | No | Yes (v9.2) |

**Practical constraint**: You can count up to 6 events simultaneously using the programmable counters, plus the dedicated cycle counter (PMCCNTR) always runs in the background. If you specify more than 7 total events, the kernel will multiplex and scale the counts -- this introduces estimation error, so keep groups to 7 or fewer for precise work.

The number of programmable counters is implementation-defined. Neoverse N1, N2, and V2 all implement 6. Read the count from `PMCR_EL0.N` or check `perf list` output.

---

## Quick Start: The Essential Five

If you only run one `perf stat` command, make it this one. These five metrics immediately tell you where to focus.

```bash
perf stat -e cycles,instructions,cache-misses,branch-misses,dTLB-load-misses \
    -- ./your_application
```

| Metric | What it tells you | Healthy range |
|---|---|---|
| **IPC** (instructions / cycles) | Overall pipeline efficiency | > 1.5 is good; < 0.5 means stalls dominate |
| **cache-misses** | L1D miss rate (maps to `L1D_CACHE_REFILL`) | < 5% of `cache-references` |
| **branch-misses** | Branch misprediction rate (`BR_MIS_PRED_RETIRED`) | < 2% of `branch-instructions` |
| **dTLB-load-misses** | TLB miss rate (`L1D_TLB_REFILL`) | Workload-dependent; high = page size problem |

---

## Generic Events → ARM PMU Mapping

Linux `perf` maps generic event names to architecture-specific PMU event codes. Here is the mapping for Neoverse cores:

| Generic `perf` event | ARM PMU event | Event code | Notes |
|---|---|---|---|
| `cpu-cycles` / `cycles` | `CPU_CYCLES` | 0x0011 | Architectural event. Also counted by the dedicated PMCCNTR cycle counter, which does not consume a programmable counter slot. |
| `instructions` | `INST_RETIRED` | 0x0008 | Architecturally executed instructions. Unlike x86, there is no dedicated fixed-function instruction counter -- this uses a programmable counter. |
| `cache-references` | `L1D_CACHE` | 0x0004 | L1 data cache accesses (loads and stores). Counts each access including multiple accesses from LDM/STM. |
| `cache-misses` | `L1D_CACHE_REFILL` | 0x0003 | L1 data cache refills from speculatively executed loads/stores. One event per cache line, excludes prefetch-induced refills. |
| `branch-instructions` | `BR_RETIRED` | 0x0021 | All architecturally executed branch instructions. |
| `branch-misses` | `BR_MIS_PRED_RETIRED` | 0x0022 | Mispredicted branches, architecturally executed. Each costs a full pipeline flush. |

**Key difference from x86**: On x86, `cycles` and `instructions` both use dedicated fixed-function counters that do not consume a programmable PMC slot. On ARM, only `CPU_CYCLES` has a dedicated counter (PMCCNTR). `INST_RETIRED` must use one of the 6 programmable counters, leaving 5 for other events when you measure instructions.

---

## Detailed Event Reference by Category

### Pipeline & Instruction Flow

These events form the backbone of all performance analysis. Start here.

| Event Name | Code | What it measures |
|---|---|---|
| **`CPU_CYCLES`** | 0x0011 | Processor cycles. Also counted by the fixed PMCCNTR register without using a programmable counter. |
| **`INST_RETIRED`** | 0x0008 | Architecturally executed instructions. This is the denominator for IPC. |
| **`INST_SPEC`** | 0x001B | Speculatively executed instructions (includes those later squashed). Compare with `INST_RETIRED` to measure speculation overhead: `(INST_SPEC - INST_RETIRED) / INST_SPEC`. |
| **`OP_RETIRED`** | 0x003C | Micro-ops retired. On Neoverse, some instructions decode to multiple micro-ops. If `OP_RETIRED >> INST_RETIRED`, the hot code uses complex multi-uop instructions. |
| **`OP_SPEC`** | 0x003B | Micro-ops speculatively executed. |
| **`EXC_TAKEN`** | 0x0009 | Exceptions taken (interrupts, syscalls, faults). High count indicates OS overhead. |

**How to use**: Compute IPC = `INST_RETIRED` / `CPU_CYCLES`. IPC below 1.0 on N1 or below 1.5 on N2/V2 (wider pipelines) suggests significant stalls.

```bash
perf stat -e cpu_cycles,inst_retired,inst_spec,op_retired -- ./app
```

---

### Stall Cycle Analysis

These events tell you *why* the pipeline is stalled. This is the most actionable category.

| Event Name | Code | Generation | What it measures |
|---|---|---|---|
| **`STALL_FRONTEND`** | 0x0023 | N1, N2, V2 | Cycles where the frontend could not deliver instructions to the backend. Causes: I-cache miss, I-TLB miss, branch mispredict recovery. |
| **`STALL_BACKEND`** | 0x0024 | N1, N2, V2 | Cycles where the backend could not accept instructions. Causes: D-cache miss stalls, execution unit busy, register file full, store buffer full. |
| **`STALL_SLOT_FRONTEND`** | 0x4004 | N2, V2 | Slot-based frontend stall metric. Counts empty dispatch slots due to frontend starvation. Required for accurate Arm Top-Down analysis. |
| **`STALL_SLOT_BACKEND`** | 0x4005 | N2, V2 | Slot-based backend stall metric. Counts empty dispatch slots due to backend pressure. |
| **`STALL_SLOT`** | 0x4006 | N2, V2 | Total wasted dispatch slots (frontend + backend + bad speculation). Used as the denominator for Top-Down Level 1 percentages. |

**Arm Top-Down Methodology**: On N2 and V2, the slot-based events enable `perf stat --topdown`, which breaks performance into four Level 1 categories:

- **Frontend Bound** = `STALL_SLOT_FRONTEND` / (`CPU_CYCLES` * pipeline_width)
- **Backend Bound** = `STALL_SLOT_BACKEND` / (`CPU_CYCLES` * pipeline_width)
- **Retiring** = `OP_RETIRED` / (`CPU_CYCLES` * pipeline_width)
- **Bad Speculation** = 1 - Frontend - Backend - Retiring

On N1, only cycle-based stall events (`STALL_FRONTEND` / `STALL_BACKEND`) are available, so top-down analysis is less precise.

```bash
# N2/V2: Full top-down Level 1
perf stat --topdown -- ./app

# N1: Cycle-based stall breakdown
perf stat -e cpu_cycles,inst_retired,stall_frontend,stall_backend -- ./app

# N2/V2: Explicit slot-based analysis
perf stat -e cpu_cycles,inst_retired,stall_slot_frontend,stall_slot_backend,stall_slot -- ./app
```

---

### Cache Hierarchy

For cache optimization work, these events are essential.

| Event Name | Code | What it measures |
|---|---|---|
| **`L1D_CACHE`** | 0x0004 | L1D cache accesses (loads and stores). Each access to a cache line is counted, including multiple accesses from LDM/STM and accesses to refill/write buffers. |
| **`L1D_CACHE_REFILL`** | 0x0003 | L1D cache refills. One event per cache line miss. Does not count prefetch-induced refills. Divide by `L1D_CACHE` for L1D miss rate. |
| **`L1D_CACHE_WB`** | 0x0015 | L1D dirty cache line write-backs to L2. High count indicates heavy write traffic or working set exceeding L1D. |
| **`L2D_CACHE`** | 0x0016 | L2 cache accesses (demand + prefetch). |
| **`L2D_CACHE_REFILL`** | 0x0017 | L2 cache refills from L3 or beyond. Divide by `L2D_CACHE` for L2 miss rate. |
| **`L2D_CACHE_WB`** | 0x0018 | L2 cache write-backs to the next level. |
| **`L3D_CACHE`** | 0x002B | L3 cache accesses (implementation-defined, available on N1/N2/V2 when L3 is present). |
| **`L3D_CACHE_REFILL`** | 0x002A | L3 cache refills from memory. This is the most expensive data path. |
| **`L1I_CACHE_REFILL`** | 0x0001 | L1 instruction cache refills. High count means the instruction footprint exceeds L1I (64 KB). |
| **`L1I_CACHE`** | 0x0014 | L1 instruction cache accesses. |
| **`MEM_ACCESS`** | 0x0013 | Data memory accesses. Counts loads and stores issued to the memory system, providing a coarse view of memory traffic. |

**How to use -- cache hierarchy efficiency**:
```bash
# Full cache picture
perf stat -e l1d_cache,l1d_cache_refill,l2d_cache,l2d_cache_refill,l3d_cache_refill -- ./app

# L1D miss rate = L1D_CACHE_REFILL / L1D_CACHE
# L2 miss rate  = L2D_CACHE_REFILL / L2D_CACHE
```

**Cache sizes by generation**:

| Level | Neoverse N1 | Neoverse N2 | Neoverse V2 |
|---|---|---|---|
| L1D | 64 KB | 64 KB | 64 KB |
| L1I | 64 KB | 64 KB | 64 KB |
| L2 (per-core) | 1 MB | 512 KB | 2 MB |
| L3 (shared) | Implementation-defined | Implementation-defined | Implementation-defined |

---

### TLB & Address Translation

Direct evidence for page-size tuning decisions.

| Event Name | Code | What it measures |
|---|---|---|
| **`L1D_TLB_REFILL`** | 0x0005 | L1 data TLB refill (page table walk or L2 TLB lookup). Each refill costs 7-30+ cycles depending on TLB level and whether a full page walk is needed. |
| **`L1I_TLB_REFILL`** | 0x0002 | L1 instruction TLB refill. High count means the code footprint spans too many pages. |
| **`L1D_TLB`** | 0x0025 | L1 data TLB accesses. Denominator for data TLB miss rate. |
| **`L1I_TLB`** | 0x0026 | L1 instruction TLB accesses. |
| **`L2D_TLB_REFILL`** | 0x002D | L2 data TLB refill -- the L2 TLB also missed, triggering a full page table walk. This is the expensive path. |
| **`L2D_TLB`** | 0x002F | L2 data TLB accesses. |
| **`DTLB_WALK`** | 0x0034 | Data TLB page table walk. Implementation-defined on some cores. Equivalent to or subset of `L2D_TLB_REFILL`. |
| **`ITLB_WALK`** | 0x0035 | Instruction TLB page table walk. |

**Page size impact**: ARM supports 4 KB, 16 KB (Graviton 4 default), 64 KB, 2 MB, and 1 GB pages. DPDK workloads benefit from 2 MB or 1 GB hugepages to minimize TLB pressure. If `L1D_TLB_REFILL` / `L1D_TLB` exceeds 1%, investigate hugepage allocation.

```bash
# TLB health check
perf stat -e l1d_tlb,l1d_tlb_refill,l2d_tlb_refill,l1i_tlb_refill -- ./app

# DTLB miss rate = L1D_TLB_REFILL / L1D_TLB
# Full page walk rate = L2D_TLB_REFILL / L1D_TLB_REFILL
```

---

### Branch Prediction

| Event Name | Code | What it measures |
|---|---|---|
| **`BR_RETIRED`** | 0x0021 | All architecturally executed branches. Denominator for misprediction rate. |
| **`BR_MIS_PRED_RETIRED`** | 0x0022 | Mispredicted branches that were architecturally executed. Each misprediction flushes the pipeline, costing ~11 cycles on N1 and ~13 cycles on N2/V2 (deeper pipelines). |
| **`BR_MIS_PRED`** | 0x0010 | Speculatively executed mispredicted branches (includes squashed). Higher than `BR_MIS_PRED_RETIRED` because it counts speculative path branches too. |
| **`BR_PRED`** | 0x0012 | Predictable branches speculatively executed. |
| **`BR_INDIRECT_SPEC`** | 0x007A | Indirect branches speculatively executed. High count with high `BR_MIS_PRED` suggests indirect branch target misprediction. |
| **`BR_IMMED_RETIRED`** | 0x006D | Immediate (direct) branches retired. |
| **`BR_RETURN_RETIRED`** | 0x006E | Return instructions retired. |

```bash
# Branch prediction efficiency
perf stat -e br_retired,br_mis_pred_retired,cpu_cycles -- ./app
# Misprediction rate = BR_MIS_PRED_RETIRED / BR_RETIRED  (target: < 2%)
```

---

### Memory Subsystem

| Event Name | Code | Generation | What it measures |
|---|---|---|---|
| **`BUS_ACCESS`** | 0x0019 | All | Bus access from the core (typically L2 miss, goes to interconnect/L3/memory). |
| **`MEM_ACCESS`** | 0x0013 | All | Data memory accesses (loads + stores issued to the memory system). |
| **`BUS_CYCLES`** | 0x001D | All | Bus cycle count. Ratio `BUS_ACCESS / BUS_CYCLES` indicates bus utilization. |
| **`REMOTE_ACCESS`** | 0x0031 | All (multi-socket) | Accesses to another socket's memory or cache. **This is the most expensive data path.** High count indicates a NUMA locality problem. |
| **`MEM_ACCESS_RD`** | 0x0066 | N2, V2 | Read memory accesses. Allows separating read vs write traffic. |
| **`MEM_ACCESS_WR`** | 0x0067 | N2, V2 | Write memory accesses. |

```bash
# Memory subsystem pressure
perf stat -e bus_access,mem_access,remote_access,l3d_cache_refill -- ./app
```

---

### SIMD / SVE

| Event Name | Code | Generation | What it measures |
|---|---|---|---|
| **`ASE_SPEC`** | 0x8005 | N1, N2, V2 | Advanced SIMD (NEON) operations speculatively executed. Indicates NEON vectorization activity. |
| **`SVE_INST_SPEC`** | 0x8006 | N2, V2 | SVE instructions speculatively executed. Zero on N1 (no SVE). If this is zero on N2/V2, your compiler is not generating SVE code. |
| **`SVE_INST_RETIRED`** | 0x0080 | N2, V2 | SVE instructions architecturally executed. Compare with `INST_RETIRED` for SVE utilization ratio. |
| **`ASE_INST_SPEC`** | 0x8005 | N1, N2, V2 | Alias for `ASE_SPEC`. NEON instructions speculatively executed. |
| **`FP_SPEC`** | 0x8003 | All | Floating-point operations speculatively executed (scalar). |
| **`VFP_SPEC`** | 0x8001 | All | VFP (floating-point) instructions speculatively executed. |

**SVE vector length**: N2 and V2 implement 128-bit SVE2. While the vector length matches NEON, SVE2 provides additional instructions (gather/scatter, predication, BFloat16) that can improve code generation. Check SVE utilization to verify your compiler is targeting SVE2:

```bash
# SIMD/SVE utilization check
perf stat -e inst_retired,ase_spec,sve_inst_spec,fp_spec -- ./app
# If SVE is available but sve_inst_spec = 0:
#   -> Recompile with -march=armv9-a+sve2 or -mcpu=neoverse-n2
```

---

## Using `perf c2c` for False Sharing Detection

This is the single most important tool for cache-line contention analysis. `perf c2c` identifies cache lines bounced between cores -- the hallmark of false sharing.

ARM uses 64-byte cache lines (same as x86), so the false-sharing boundary is every 64 bytes.

```bash
# Record cache-to-cache transfer events (needs root or perf_event_paranoid <= 0)
# On ARM, perf c2c requires SPE to provide data address information
perf c2c record -e arm_spe_0/ts_enable=1,load_filter=1,store_filter=1,min_latency=50/ \
    -- ./app

# Analyze
perf c2c report --stdio

# Look for:
#   "Shared Data Cache Line Table" -- sorted by cache line address
#   High "HITM" counts = cache line modified by one core, read by another
```

**What to look for**: The `HITM` (Hit Modified) column shows cache lines experiencing coherency traffic. On ARM, `perf c2c` relies on SPE (Statistical Profiling Extension) for data address attribution rather than the hardware-assisted approach used on x86 (PEBS). This means SPE must be available and enabled in the kernel.

**Requirement**: `perf c2c` on ARM requires Linux 5.18+ with SPE support and `CONFIG_ARM_SPE_PMU=y` in the kernel config.

---

## Statistical Profiling Extension (SPE)

SPE is ARM's equivalent of Intel PEBS (Precise Event-Based Sampling). Available on all Neoverse server cores (N1 and later), SPE provides per-instruction profiling data that the standard PMU cannot deliver.

### What SPE captures per sampled operation

- **Program counter** -- which instruction was sampled
- **Data virtual address** -- the address accessed by load/store operations
- **Data physical address** -- with `pa_enable=1` (requires elevated privilege)
- **Latency** -- cycles from issue to completion for the sampled operation
- **Data source** -- where the data came from (L1, L2, L3, DRAM, remote)
- **Events** -- cache miss, TLB miss, branch mispredict, etc.
- **Operation type** -- load, store, branch, SVE, etc.

### Basic SPE usage

```bash
# Record with SPE (sample loads and stores)
perf record -e arm_spe_0// -- ./app

# Record with filters and options
perf record -e arm_spe_0/ts_enable=1,pa_enable=1,load_filter=1,store_filter=1,min_latency=50/ \
    -- ./app

# Analyze: top functions by memory access latency
perf report --sort=symbol,mem

# Memory access data source breakdown
perf mem record -- ./app
perf mem report --sort=mem --stdio
```

### SPE filter options

| Option | Effect |
|---|---|
| `ts_enable=1` | Include timestamps in samples |
| `pa_enable=1` | Include physical addresses (needs root) |
| `load_filter=1` | Only sample load operations |
| `store_filter=1` | Only sample store operations |
| `branch_filter=1` | Only sample branch operations |
| `min_latency=N` | Only record samples with latency >= N cycles |

### SPE vs standard PMU sampling

| Feature | Standard PMU (`-e cycles`) | SPE (`-e arm_spe_0//`) |
|---|---|---|
| Attribution accuracy | Statistical (skid possible) | Precise (no skid) |
| Data address | No | Yes |
| Latency per sample | No | Yes |
| Data source (L1/L2/DRAM) | No | Yes |
| Overhead | Low | Moderate (buffer writes) |
| Availability | All ARM cores | Neoverse N1+ (ARMv8.2-SPE) |

**SPE is a key differentiator for ARM profiling.** It provides capabilities that on x86 require combining PEBS, LBR, and multiple PMU event groups. Use SPE whenever you need to understand memory access patterns, latency distributions, or data locality.

---

## Recommended Analysis Workflows

### Workflow 1: Top-Down Performance Triage

```bash
# Step 1: Overall health check
perf stat -d -- ./app
# -d adds L1-dcache-loads/misses, LLC-loads/misses, dTLB-loads/misses, etc.

# Step 2: Stall breakdown (N2/V2 -- slot-based)
perf stat --topdown -- ./app

# Step 2 alt: Stall breakdown (N1 -- cycle-based)
perf stat -e cpu_cycles,inst_retired,stall_frontend,stall_backend -- ./app

# Step 3: If cache-bound, drill into cache hierarchy
perf stat -e l1d_cache,l1d_cache_refill,l2d_cache,l2d_cache_refill,l3d_cache_refill,l1i_cache_refill -- ./app

# Step 4: If TLB-bound, drill into TLB levels
perf stat -e l1d_tlb,l1d_tlb_refill,l2d_tlb_refill,l1i_tlb_refill -- ./app

# Step 5: If contention-bound, use SPE + c2c
perf c2c record -e arm_spe_0// -- ./app && perf c2c report --stdio
```

### Workflow 2: Cache Analysis with SPE

```bash
# Record load latency distribution
perf record -e arm_spe_0/load_filter=1,min_latency=20/ -- ./app

# Report by data source (L1, L2, L3, DRAM)
perf report --sort=mem --stdio

# Identify hot memory addresses
perf report --sort=symbol,dso,mem --stdio

# For NUMA analysis, enable physical address capture
perf record -e arm_spe_0/pa_enable=1,load_filter=1,min_latency=100/ -- ./app
```

### Workflow 3: NUMA Locality (Multi-Socket)

```bash
# Check for cross-socket memory traffic
perf stat -e remote_access,bus_access,l3d_cache_refill,cpu_cycles \
    -- numactl --cpunodebind=0 --membind=0 ./app
perf stat -e remote_access,bus_access,l3d_cache_refill,cpu_cycles \
    -- numactl --cpunodebind=0 --membind=1 ./app
# Second run should show dramatically more remote_access and higher cycle counts
```

### Workflow 4: Hot Function Profiling

```bash
# Record with call graph (frame pointers recommended on ARM)
perf record -g --call-graph fp -e cpu_cycles -- ./app
perf report

# Annotate assembly of the hot function
perf annotate --symbol=hot_function

# SPE-based profiling for memory-bound functions
perf record -e arm_spe_0/load_filter=1,min_latency=50/ -- ./app
perf report --sort=symbol --stdio
```

---

## Cross-Generation Differences

| Topic | Neoverse N1 (Graviton 2) | Neoverse N2 (Graviton 3E, Yitian 710) | Neoverse V2 (Grace, Graviton 4) |
|---|---|---|---|
| Pipeline width | 4-wide decode/dispatch | 5-wide decode/dispatch | 5-wide decode, 8-wide dispatch |
| Top-down method | Cycle-based (`STALL_FRONTEND`/`STALL_BACKEND`) | Slot-based (`STALL_SLOT_*`) via `perf stat --topdown` | Slot-based (`STALL_SLOT_*`) via `perf stat --topdown` |
| SPE | Yes (v8.2) | Yes (v8.2) | Yes (v8.2, enhanced filtering) |
| SVE | No | SVE2, 128-bit | SVE2, 128-bit |
| BRBE (Branch Record) | No | No | Yes (v9.2) -- hardware branch record buffer |
| L1D / L1I | 64 KB / 64 KB | 64 KB / 64 KB | 64 KB / 64 KB |
| L2 (per-core) | 1 MB | 512 KB | 2 MB |
| Branch mispredict cost | ~11 cycles | ~13 cycles | ~13 cycles |
| ISA level | ARMv8.2-A | ARMv9.0-A | ARMv9.0-A |
| `MEM_ACCESS_RD`/`WR` events | No | Yes | Yes |
| `BR_INDIRECT_SPEC` | Implementation-defined | Yes | Yes |

**N3 and V3 notes**: Neoverse N3 and V3 (shipping 2025+) extend the PMU with additional SVE-specific events (`SVE_MATH_SPEC`, gather/scatter counters) and enhanced BRBE capabilities. The core PMUv3 event codes remain the same -- new events are additive. Check `perf list` on the target system for the definitive event list.

---

## Discovering Available Events on Your System

```bash
# List all PMU events available on this specific CPU
perf list pmu

# List with full descriptions
perf list --long-desc pmu

# List ARM architectural events
perf list | grep armv8_pmuv3

# Show raw event encoding for a named event
perf stat -e armv8_pmuv3/event=0x0003/ -v -- true 2>&1 | grep config

# Check SPE availability
ls /sys/devices/arm_spe_0/ 2>/dev/null && echo "SPE available" || echo "SPE not available"

# Check top-down support (N2/V2)
perf stat --topdown -- true 2>&1 | head -5
```
