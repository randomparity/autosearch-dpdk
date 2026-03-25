# s390x `perf` Performance Counter Reference

## z/Architecture CPUMF Event Guide for z15 / z16 / z17

Companion document to the s390x optimization checklist. This reference explains the CPU Measurement Facility (CPUMF) counter sets, maps generic `perf` events to CPUMF counter numbers, and provides analysis workflows for z/Architecture systems running Linux.

---

## CPUMF Architecture Overview

IBM z/Architecture processors implement the CPU Measurement Facility (CPUMF), a hardware performance monitoring subsystem fundamentally different from the PMU designs on x86 or POWER. Instead of a small set of programmable PMC registers, CPUMF provides **counter sets** -- groups of counters that are enabled or disabled as a unit.

### Counter Sets

| Counter Set | Counter Range | What it covers |
|---|---|---|
| **Basic** | 0--5 | CPU cycles, instructions, L1I/L1D cache directory writes and penalty cycles |
| **Problem-State** | 32--33 | User-space (problem-state) cycles and instructions only |
| **Crypto-Activity** | 64--83 | CPACF crypto engine utilization: AES, SHA, DEA, ECC, PRNG function counts, cycles, and blocked counts |
| **Extended** | 128--279+ | Cache hierarchy sourcing, TLB, transactions, deflate, NNPA (AI accelerator), branch prediction, SMT diagnostics |
| **MT-Diagnostic** | 448--449 | Multi-threading cycle counts (one thread active, two threads active) |

**Key difference from x86/POWER**: No "programmable PMC slots." You enable an entire counter set, and all its counters are counted simultaneously with **no multiplexing** -- always precise. The tradeoff is that you cannot create arbitrary event groups.

### Authorization

Counter sets must be authorized at the LPAR activation profile level (HMC Security settings). Verify from Linux:

```bash
lscpumf -i    # Shows authorized counter sets and CPUMF version
```

If a counter set is not authorized, `perf stat` returns zero or an error for events in that set.

### Hardware Generations

| Feature | z15 (8561/8562) | z16 (3931) | z17 (9175) |
|---|---|---|---|
| Frequency | 5.2 GHz | 5.2 GHz | 5.5 GHz |
| Cores per CP chip | 12 | 8 | 8 |
| L1I / L1D (per core) | 128 KB / 128 KB | 128 KB / 128 KB | 128 KB / 128 KB |
| L2 (per core) | 4 MB I + 4 MB D | 32 MB unified | 36 MB unified |
| L3 | 256 MB shared/chip | Virtual L3 (up to 224 MB/chip) | Virtual L3 (up to 360 MB/chip) |
| L4 | 960 MB shared/drawer | Virtual L4 (up to 1.75 GB/drawer) | Virtual L4 |
| Cache line size | 256 bytes | 256 bytes | 256 bytes |
| SMT | 2-way | 2-way | 2-way |
| On-chip AI accelerator | No | Yes (NNPA) | Yes (NNPA, enhanced) |
| Chip technology | 14 nm | 7 nm (Samsung) | 5 nm (Samsung) |
| CPUMF version | 6 | 7 | 7+ |
| Kernel event dir | `cf_z15` | `cf_z16` | `cf_z17` |

**z16 cache architecture change**: z16 replaced the traditional shared L3/L4 with a "virtual victim" design. Each core's 32 MB L2 is the primary working set; evicted lines can be found in another core's L2 (virtual L3) or another chip's L2 (virtual L4). Extended counter events that reference "L3 sourced" and "L4 sourced" have different physical meanings on z15 vs z16/z17.

---

## Quick Start: The Essential Five

If you only run one `perf stat` command, make it this one. These five metrics immediately tell you where to focus.

```bash
perf stat -e cpum_cf/CPU_CYCLES/,cpum_cf/INSTRUCTIONS/,\
cpum_cf/L1D_DIR_WRITES/,cpum_cf/L1D_PENALTY_CYCLES/,\
cpum_cf/L1I_PENALTY_CYCLES/ \
    -- ./your_application
```

| Metric | What it tells you | Healthy range |
|---|---|---|
| **IPC** (INSTRUCTIONS / CPU_CYCLES) | Overall pipeline efficiency | > 1.0 is good; < 0.5 means stalls dominate |
| **L1D penalty ratio** (L1D_PENALTY_CYCLES / CPU_CYCLES) | Fraction of time stalled on data cache | < 10% |
| **L1I penalty ratio** (L1I_PENALTY_CYCLES / CPU_CYCLES) | Fraction of time stalled on instruction cache | < 5% |
| **L1D miss rate** (L1D_DIR_WRITES / INSTRUCTIONS) | Data cache refill pressure | Workload-dependent; high = cache optimization needed |

**Syntax note**: On s390x, events are accessed through the `cpum_cf` PMU. Use `cpum_cf/<EventName>/` for symbolic names, or `cpum_cf/event=<N>/` for counter numbers. Raw event syntax (`r<hex>`) is deprecated since kernel 5.5.

---

## Generic Events to CPUMF Mapping

Linux `perf` maps some generic event names to s390x CPUMF counters. This mapping is limited compared to x86 -- many generic events are **not available** on s390x.

| Generic `perf` event | CPUMF counter | Counter # | Notes |
|---|---|---|---|
| `cpu-cycles` / `cycles` | `CPU_CYCLES` | 0 | Excludes wait-state (idle) cycles |
| `instructions` | `INSTRUCTIONS` | 1 | All instructions executed |
| `cache-references` | Not mapped | -- | Use `L1D_DIR_WRITES` (counter 4) as a proxy |
| `cache-misses` | Not mapped | -- | Use `L1D_PENALTY_CYCLES` (counter 5) or extended set events |
| `branch-instructions` | Not mapped | -- | No basic-set branch counter; z17 adds `WRONG_BRANCH_PREDICTION` (counter 206) in extended set |
| `branch-misses` | Not mapped | -- | z17 extended set only |

**Key difference from x86/POWER**: Most generic hardware cache events (`L1-dcache-loads`, `dTLB-load-misses`, etc.) are **not supported** on s390x. You must use CPUMF symbolic names directly (`cpum_cf/L1D_DIR_WRITES/` instead of `L1-dcache-load-misses`). This is the biggest "gotcha" when porting perf workflows from x86.

---

## Detailed Event Reference by Category

### ★ Critical — Pipeline & Instruction Flow

These events form the backbone of all performance analysis. Start here.

| Event Name | Counter # | Set | What it measures |
|---|---|---|---|
| **`CPU_CYCLES`** | 0 | Basic | Total CPU cycles, excluding wait-state (idle). This is your cycle denominator for IPC and all ratio calculations. |
| **`INSTRUCTIONS`** | 1 | Basic | Total instructions executed. On s390x, one "instruction" can be a complex CISC operation (e.g., MVCL can move megabytes). IPC interpretation differs from RISC architectures. |
| **`PROBLEM_STATE_CPU_CYCLES`** | 32 | Problem-State | Cycles in problem state (user space) only, excluding wait-state. Use this to isolate application time from kernel/hypervisor overhead. |
| **`PROBLEM_STATE_INSTRUCTIONS`** | 33 | Problem-State | Instructions executed in problem state only. |

**How to use**: IPC = `INSTRUCTIONS` / `CPU_CYCLES`. Above 1.0 is typical for well-optimized code; below 0.5 indicates significant stalls.

```bash
perf stat -e cpum_cf/CPU_CYCLES/,cpum_cf/INSTRUCTIONS/,\
cpum_cf/PROBLEM_STATE_CPU_CYCLES/,cpum_cf/PROBLEM_STATE_INSTRUCTIONS/ -- ./app
```

---

### ★ Critical — Stall Cycle Analysis

Unlike x86 (which has TopDown methodology counters) or POWER (which has `PM_CMPLU_STALL` sub-events), s390x provides stall information through **penalty cycle counters** in the basic set. These directly measure cycles lost to cache misses.

| Event Name | Counter # | Set | What it measures |
|---|---|---|---|
| **`L1I_PENALTY_CYCLES`** | 3 | Basic | Cycles stalled due to L1 instruction cache misses. High values indicate the code footprint exceeds L1I (128 KB) or suffers from branch mispredictions disrupting prefetch. |
| **`L1D_PENALTY_CYCLES`** | 5 | Basic | Cycles stalled due to L1 data cache misses. This is the primary stall indicator for memory-bound workloads. |

The penalty counters are cumulative -- they include all cycles lost waiting for data from L2 through memory. To determine *where* data came from, use the extended set's DCW sourcing events.

```bash
perf stat -e cpum_cf/CPU_CYCLES/,cpum_cf/INSTRUCTIONS/,\
cpum_cf/L1I_PENALTY_CYCLES/,cpum_cf/L1D_PENALTY_CYCLES/ -- ./app
# Stall fractions: L1D_PENALTY_CYCLES/CPU_CYCLES, L1I_PENALTY_CYCLES/CPU_CYCLES
```

---

### ★ Critical — Cache Hierarchy

z/Architecture uses **256-byte cache lines** -- 4x the size of x86 (64 bytes) and 2x POWER (128 bytes). This has profound implications for data structure layout, false sharing, and prefetch behavior.

The extended counter set tracks *where* L1 refills came from. The naming convention encodes topology: **On-Chip** (fastest) -> **On-Module/Cluster** -> **On-Drawer** -> **Off-Drawer** (most expensive, cross-book interconnect).

#### L1D Refill Sourcing (z15)

| Event Name | Counter # | What it measures |
|---|---|---|
| **`L1D_ONCHIP_L3_SOURCED_WRITES`** | 144 | L1D refill from on-chip L3 cache |
| `L1D_ONCHIP_MEMORY_SOURCED_WRITES` | 145 | L1D refill from on-chip local memory |
| `L1D_ONCHIP_L3_SOURCED_WRITES_IV` | 146 | L1D refill from on-chip L3 with intervention (modified by another core) |
| `L1D_ONCLUSTER_L3_SOURCED_WRITES` | 147 | L1D refill from on-cluster L3 |
| `L1D_OFFCLUSTER_L3_SOURCED_WRITES` | 150 | L1D refill from off-cluster L3 |
| **`L1D_OFFDRAWER_L3_SOURCED_WRITES`** | 153 | L1D refill from off-drawer L3 |
| **`L1D_OFFDRAWER_MEMORY_SOURCED_WRITES`** | 154 | L1D refill from off-drawer memory (most expensive) |
| `L1D_ONDRAWER_L4_SOURCED_WRITES` | 156 | L1D refill from on-drawer L4 cache |

Additional sourcing events exist for on-cluster memory (148), off-cluster memory (151), and off-drawer L4 (157). Corresponding `L1I_*` events (counters 162--175) track instruction cache refills with the same topology breakdown.

#### L1D Refill Sourcing (z16/z17)

On z16/z17, the naming shifted to reflect the virtual-cache topology. Events use `DCW_` prefix and include "Chip HP Hit" / "Drawer HP Hit" suffixes for horizontal persistence (data found in another core's L2):

| Event Name | Counter # | What it measures |
|---|---|---|
| **`DCW_REQ`** | 145 | Total directory writes to L1D from L2 cache |
| `DCW_REQ_IV` | 146 | L1D refill from L2 with intervention |
| `DCW_REQ_CHIP_HIT` | 147 | L1D refill with chip-level horizontal persistence hit |
| `DCW_REQ_DRAWER_HIT` | 148 | L1D refill with drawer-level horizontal persistence hit |
| `DCW_ON_CHIP` | 149 | L1D refill from on-chip L2 |
| **`DCW_OFF_DRAWER`** | 155 | L1D refill from off-drawer L2 (most expensive cache path) |
| `DCW_ON_CHIP_MEMORY` | 156 | L1D refill from on-chip memory |
| **`DCW_OFF_DRAWER_MEMORY`** | 159 | L1D refill from off-drawer memory (most expensive) |

Additional events: `DCW_ON_CHIP_IV` (150), `DCW_ON_MODULE` (153), `DCW_ON_DRAWER` (154), `DCW_ON_DRAWER_MEMORY` (158). Corresponding `ICW_*` events (counters 169--183) and `IDCW_*` events (160--168) track instruction cache and combined I/D cache refills.

```bash
# z16/z17: Where are L1D refills coming from?
perf stat -e cpum_cf/L1D_DIR_WRITES/,cpum_cf/DCW_REQ/,\
cpum_cf/DCW_ON_CHIP/,cpum_cf/DCW_OFF_DRAWER/,\
cpum_cf/DCW_OFF_DRAWER_MEMORY/ -- ./app
# z15: Replace DCW_* with L1D_ONCHIP_L3_SOURCED_WRITES etc.
```

**256-byte cache line impact**: A single L1D miss fetches 256 bytes. For packet processing, this means a 64-byte packet descriptor plus its adjacent 192 bytes all arrive in one miss. Structure padding and alignment to 256-byte boundaries is the primary cache optimization lever on s390x.

---

### ★ Critical — TLB & Address Translation

s390x uses Dynamic Address Translation (DAT), a multi-level page table walk. The TLB hierarchy has two levels: a first-level TLB (integrated into L1) and a second-level TLB2. The extended counter set exposes TLB2 activity.

| Event Name | Counter # | What it measures |
|---|---|---|
| **`DTLB2_WRITES`** | 129 | Data TLB2 refills. Primary DTLB pressure indicator. |
| **`DTLB2_MISSES`** | 130 | Data TLB2 misses requiring full DAT table walk. |
| `CRSTE_1MB_WRITES` / `DTLB2_HPAGE_WRITES` | 131 | TLB2 entries for 1 MB large pages (z16+ / z15 naming). |
| `DTLB2_GPAGE_WRITES` | 132 | TLB2 entries for 2 GB huge pages. |
| **`ITLB2_WRITES`** / **`ITLB2_MISSES`** | 134 / 135 | Instruction TLB2 refills and misses. |
| `TLB2_PTE_WRITES` / `TLB2_CRSTE_WRITES` | 137 / 138 | Page table entry and region/segment table entry writes. |
| `TLB2_ENGINES_BUSY` | 139 | Cycles TLB2 translation engines are busy (DAT walk contention). |
| `L1C_TLB2_MISSES` | 143 | Compound miss: L1 cache miss that also missed in TLB2. |

**s390x page sizes**: 4 KB (standard), 1 MB (large, via `hugetlbfs`), 2 GB (huge). For DPDK, 1 MB pages are the most common choice.

```bash
perf stat -e cpum_cf/DTLB2_WRITES/,cpum_cf/DTLB2_MISSES/,\
cpum_cf/ITLB2_MISSES/,cpum_cf/TLB2_ENGINES_BUSY/,cpum_cf/CPU_CYCLES/ -- ./app
# DTLB2 miss rate = DTLB2_MISSES / DTLB2_WRITES
# DAT walk bottleneck = TLB2_ENGINES_BUSY / CPU_CYCLES
```

---

### Important — Branch Prediction

z/Architecture branch prediction counters are limited compared to x86 or POWER. The basic and problem-state counter sets do not include branch events. The z17 extended counter set adds the first branch prediction counter.

| Event Name | Counter # | Generation | What it measures |
|---|---|---|---|
| **`WRONG_BRANCH_PREDICTION`** | 206 | z17 only | Incorrect branch predictions. This is the first directly-exposed branch misprediction counter on z/Architecture. |

On z15/z16, infer branch misprediction indirectly: high `L1I_PENALTY_CYCLES` with low `L1I_DIR_WRITES` suggests pipeline flushes from mispredictions rather than I-cache misses.

```bash
# z17: Direct measurement
perf stat -e cpum_cf/INSTRUCTIONS/,cpum_cf/WRONG_BRANCH_PREDICTION/ -- ./app

# z15/z16: Indirect inference
perf stat -e cpum_cf/L1I_DIR_WRITES/,cpum_cf/L1I_PENALTY_CYCLES/ -- ./app
```

---

### Important — Memory Subsystem & NUMA

z/Architecture systems have a deep NUMA hierarchy: **core -> chip -> module/cluster -> drawer -> book interconnect**. The extended counter set's L1D/L1I sourcing events (described in the Cache Hierarchy section) are the primary tool for identifying NUMA locality problems.

**Intervention events** (`_IV` suffix) indicate data was obtained from another core that had modified it -- evidence of sharing or contention. On z16/z17, "Chip/Drawer HP Hit" suffixes indicate horizontal persistence lookups in another core's L2.

```bash
# z16/z17: Check for cross-drawer traffic
perf stat -e cpum_cf/DCW_ON_CHIP/,cpum_cf/DCW_ON_DRAWER/,\
cpum_cf/DCW_OFF_DRAWER/,cpum_cf/DCW_OFF_DRAWER_MEMORY/ \
    -- numactl --cpunodebind=0 --membind=0 ./app
# High DCW_OFF_DRAWER* = cross-drawer NUMA locality problem
```

**Cross-drawer penalty**: The latency penalty grows with each topology level crossed. Bind both CPU and memory to the same drawer via `numactl` as the first optimization step.

---

### Important — Vector Extension (VXE) & SIMD

z/Architecture supports SIMD via the Vector Extension Facility (VXE), with 128-bit vector registers. The extended counter set does not have dedicated vector instruction counters, but related events exist:

| Event Name | Counter # | Set | What it measures |
|---|---|---|---|
| `VX_BCD_EXECUTION_SLOTS` | 225 | Extended | Finished vector arithmetic Binary Coded Decimal instructions. Relevant for COBOL/decimal workloads using vector BCD. |
| `DECIMAL_INSTRUCTIONS` | 226 | Extended | Decimal instructions dispatched. |

Verify VXE usage by inspecting `perf annotate` output for `V` prefix instructions (VL, VST, VA, etc.).

---

### Unique to s390x — Crypto Acceleration

The Crypto-Activity counter set (counters 64--83) is unique to z/Architecture. It measures utilization of the CP Assist for Cryptographic Functions (CPACF), a hardware crypto accelerator integrated into every z/Architecture core.

| Event Name | Counter # | What it measures |
|---|---|---|
| **`AES_FUNCTIONS`** | 76 | AES encryption/decryption function invocations |
| **`AES_CYCLES`** | 77 | Cycles spent executing AES operations |
| `AES_BLOCKED_FUNCTIONS` | 78 | AES function calls that were blocked (contention) |
| `AES_BLOCKED_CYCLES` | 79 | Cycles blocked waiting for AES engine |
| `SHA_FUNCTIONS` | 68 | SHA hash function invocations |
| `SHA_CYCLES` | 69 | Cycles spent on SHA operations |
| `SHA_BLOCKED_FUNCTIONS` | 70 | SHA calls that were blocked |
| `SHA_BLOCKED_CYCLES` | 71 | Cycles blocked waiting for SHA engine |
| `DEA_FUNCTIONS` / `DEA_CYCLES` | 72 / 73 | DEA/3DES function invocations and cycles |
| `DEA_BLOCKED_FUNCTIONS` / `DEA_BLOCKED_CYCLES` | 74 / 75 | DEA calls and cycles blocked |
| `ECC_FUNCTION_COUNT` / `ECC_CYCLES_COUNT` | 80 / 81 | Elliptic Curve Cryptography invocations and cycles |
| `ECC_BLOCKED_FUNCTION_COUNT` / `ECC_BLOCKED_CYCLES_COUNT` | 82 / 83 | ECC calls and cycles blocked |
| `PRNG_FUNCTIONS` / `PRNG_CYCLES` | 64 / 65 | Pseudo-Random Number Generator invocations and cycles |
| `PRNG_BLOCKED_FUNCTIONS` / `PRNG_BLOCKED_CYCLES` | 66 / 67 | PRNG calls and cycles blocked |

**How to use**: The `_BLOCKED_` counters are the key diagnostic. If `AES_BLOCKED_CYCLES` / `AES_CYCLES` is high, the CPACF engine is oversubscribed -- distribute crypto work across more cores.

```bash
perf stat -e cpum_cf/AES_FUNCTIONS/,cpum_cf/AES_CYCLES/,\
cpum_cf/AES_BLOCKED_CYCLES/,cpum_cf/SHA_FUNCTIONS/,cpum_cf/SHA_CYCLES/ -- ./app
# Contention ratio = AES_BLOCKED_CYCLES / AES_CYCLES (target: < 5%)
```

---

### Specialized — Deflate & Accelerators (z15+)

| Event Name | Counter # | Generation | What it measures |
|---|---|---|---|
| `DFLT_ACCESS` / `DFLT_CYCLES` | 247/252 (z15), 248/253 (z16+) | z15+ | Cycles obtaining / using deflate unit |
| `DFLT_CC` / `DFLT_CCFINISH` | 264/265 (z15), 265/266 (z16+) | z15+ | DEFLATE CONVERSION CALL invocations / completions |
| `SORTL` | 256 | z16+ | SORT LISTS instruction count |
| `NNPA_INVOCATIONS` / `NNPA_COMPLETIONS` | 267 / 268 | z16+ | NNPA (AI accelerator) invocations and completions |
| `NNPA_WAIT_LOCK` / `NNPA_HOLD_LOCK` | 269 / 270 | z16+ | Cycles obtaining / holding NNPA lock |

---

### Specialized — SMT & Thread Contention

All generations support 2-way SMT. The MT-Diagnostic set has `MT_DIAG_CYCLES_ONE_THR_ACTIVE` (448) and `MT_DIAG_CYCLES_TWO_THR_ACTIVE` (449). z17 adds `CYCLES_SAMETHRD` (202), `CYCLES_DIFFTHRD` (203), `INST_SAMETHRD` (204), `INST_DIFFTHRD` (205) for solo-vs-shared IPC comparison.

```bash
# z17: SMT impact analysis
perf stat -e cpum_cf/CYCLES_SAMETHRD/,cpum_cf/CYCLES_DIFFTHRD/,\
cpum_cf/INST_SAMETHRD/,cpum_cf/INST_DIFFTHRD/ -- ./app
# Solo IPC = INST_SAMETHRD / CYCLES_SAMETHRD
# Shared IPC = INST_DIFFTHRD / CYCLES_DIFFTHRD
```

---

## Using `perf c2c` for False Sharing Detection

`perf c2c` identifies cache lines bouncing between cores due to true or false sharing. On s390x, the 256-byte cache line size makes false sharing analysis **fundamentally different** from x86.

**256-byte false sharing zone**: Any two independent variables within the same 256-byte region will false-share. This is 4x more likely than on x86 (64-byte lines). Two per-CPU counters separated by 128 bytes would avoid false sharing on x86 and POWER, but **not** on s390x.

```bash
# Record cache-to-cache transfer events (needs root or perf_event_paranoid=0)
perf c2c record -a -- sleep 10    # system-wide
perf c2c record -- ./app           # per-process

# Analyze
perf c2c report --stdio
```

**Practical note**: `perf c2c` relies on the sampling facility (`cpum_sf`). Ensure it is authorized in the LPAR profile.

**Alignment guideline**: For per-core or per-thread data on s390x, pad and align to 256 bytes (`__attribute__((aligned(256)))`). The standard `__rte_cache_aligned` macro in DPDK uses 64 or 128 bytes, which is insufficient for s390x.

---

## Sampling with `perf record`

s390x has a hardware **Sampling Facility** (`cpum_sf`) distinct from the Counter Facility (`cpum_cf`). There is no equivalent to Intel PEBS or AMD IBS. The sampling facility provides:

- **Basic-sampling mode**: Periodic instruction samples with IP, PSW (Program Status Word), and basic context. Activated with event `SF_CYCLES_BASIC` (raw event `rB0000`).
- **Diagnostic-sampling mode**: Reserved for IBM support.

```bash
# Basic sampling -- profile hot functions
perf record -e cpum_sf/SF_CYCLES_BASIC/ -- ./app
perf report

# Annotate assembly of a hot function
perf annotate --symbol=hot_function

# Adjust sampling frequency (check limits with lscpumf -i)
perf record -F 10000 -e cpum_sf/SF_CYCLES_BASIC/ -- ./app
```

**Limitations compared to x86**: No memory-address sampling (no load/store latency attribution), no data-source tagging, and no branch-record sampling (no LBR equivalent). For cache hierarchy analysis, rely on the counter facility's aggregate sourcing events.

---

## Recommended Analysis Workflows

### Workflow 1: Top-Down Performance Triage

```bash
# Step 1: Overall health (IPC + stalls)
perf stat -e cpum_cf/CPU_CYCLES/,cpum_cf/INSTRUCTIONS/,\
cpum_cf/L1I_PENALTY_CYCLES/,cpum_cf/L1D_PENALTY_CYCLES/ -- ./app

# Step 2: User-space vs kernel/hypervisor split
perf stat -e cpum_cf/CPU_CYCLES/,cpum_cf/PROBLEM_STATE_CPU_CYCLES/ -- ./app

# Step 3: Cache-bound? Drill into sourcing (z16/z17)
perf stat -e cpum_cf/DCW_REQ/,cpum_cf/DCW_ON_CHIP/,\
cpum_cf/DCW_OFF_DRAWER/,cpum_cf/DCW_OFF_DRAWER_MEMORY/ -- ./app

# Step 4: TLB-bound? Check page sizes
perf stat -e cpum_cf/DTLB2_WRITES/,cpum_cf/DTLB2_MISSES/,\
cpum_cf/CRSTE_1MB_WRITES/,cpum_cf/ITLB2_MISSES/ -- ./app

# Step 5: Contention? Run c2c
perf c2c record -- ./app && perf c2c report --stdio
```

### Workflow 2: Crypto Workload Analysis

```bash
# Which crypto engines are in use, and is there contention?
perf stat -e cpum_cf/CPU_CYCLES/,cpum_cf/AES_FUNCTIONS/,cpum_cf/AES_CYCLES/,\
cpum_cf/AES_BLOCKED_CYCLES/,cpum_cf/SHA_FUNCTIONS/,cpum_cf/SHA_CYCLES/ \
    -- ./app
# Crypto fraction = (AES_CYCLES + SHA_CYCLES) / CPU_CYCLES
# Contention ratio = AES_BLOCKED_CYCLES / AES_CYCLES (target: < 5%)
```

### Workflow 3: NUMA Locality (Multi-Drawer)

```bash
# Compare on-drawer vs off-drawer (z16/z17)
perf stat -e cpum_cf/DCW_ON_CHIP/,cpum_cf/DCW_ON_DRAWER/,\
cpum_cf/DCW_OFF_DRAWER/,cpum_cf/DCW_OFF_DRAWER_MEMORY/ \
    -- numactl --cpunodebind=0 --membind=0 ./app

# Repeat with cross-drawer memory -- expect dramatically more OFF_DRAWER events
perf stat -e cpum_cf/DCW_ON_CHIP/,cpum_cf/DCW_ON_DRAWER/,\
cpum_cf/DCW_OFF_DRAWER/,cpum_cf/DCW_OFF_DRAWER_MEMORY/ \
    -- numactl --cpunodebind=0 --membind=1 ./app
```

### Workflow 4: Hot Function Profiling

```bash
perf record -g -e cpum_sf/SF_CYCLES_BASIC/ -- ./app
perf report
perf annotate --symbol=hot_function
# Look for: MVCL/MVC (memory copy), CLST (string compare), unvectorized loops
```

---

## Big-Endian Considerations

s390x is **big-endian** -- the only big-endian Linux architecture still in active mainline use. This has several performance-relevant implications:

1. **Network byte order is native**: `htonl()`/`ntohl()` are no-ops on s390x. Packet header processing incurs zero byte-swap overhead. Little-endian protocol fields (PCIe TLP headers, some USB descriptors) require explicit swapping.

2. **Structure layout and bit fields**: Bit fields are laid out MSB-first on s390x vs LSB-first on x86. Use explicit byte/bit manipulation instead of bit fields for portable, endian-safe protocol headers.

3. **Serialization overhead**: Little-endian wire formats (Protocol Buffers, many file formats) require byte swapping. Use `__builtin_bswap32` / `__builtin_bswap64` which compile to single `LRVG`/`STRV` instructions on s390x.

4. **256-byte cache lines interact with endianness**: On s390x, incrementing a counter modifies the *end* of the field (high bytes at low addresses), opposite of x86. This affects which bytes are "hot" on partial writes within a cache line.

5. **`perf c2c` false sharing**: Data structure field ordering combined with 256-byte cache lines and big-endian layout means false sharing patterns differ from x86. Always verify with `perf c2c`.

---

## Discovering Available Events on Your System

```bash
lscpumf -c              # List authorized CPUMF counters with symbolic names
lscpumf -C              # List ALL counters (regardless of authorization)
lscpumf -i              # CPUMF version, authorization, sampling info
lscpumf -s              # Sampling facility details
perf list pmu           # List available perf events
perf stat -e cpum_cf/CPU_CYCLES/ -- true      # By symbolic name
perf stat -e cpum_cf/event=0/ -- true         # By counter number
```

---

## Cross-Generation Migration Notes

| Topic | z15 | z16 / z17 |
|---|---|---|
| Cache topology | Traditional L1/L2/L3/L4 hierarchy | Virtual victim L2/L3/L4 (modular scalable) |
| L2 per core | 4 MB I + 4 MB D (separate) | 32 MB unified (z16) / 36 MB (z17) |
| Extended counter naming | `L1D_ONCHIP_L3_SOURCED_WRITES` etc. | `DCW_ON_CHIP` etc. |
| Branch misprediction | Not available | z17 only: `WRONG_BRANCH_PREDICTION` (206) |
| SMT detail counters | `MT_DIAG_CYCLES_*` only | z17 adds `CYCLES_SAMETHRD`/`DIFFTHRD` |
| NNPA (AI accelerator) | Not available | z16+: `NNPA_INVOCATIONS`/`COMPLETIONS`; z17 adds on/off-chip detail |
| Crypto / Basic counters | 64--83 / 0--5, 32--33 | Same across all generations |
| `perf` kernel event dir | `cf_z15` | `cf_z16` / `cf_z17` |
