# aarch64 Performance Optimization Checklist

## Optimizing DPDK on Arm Neoverse (N1 / N2 / V2 / N3 / V3)

This checklist identifies the major architectural differences between amd64 (x86_64) and aarch64 that affect DPDK performance, and provides an iterative plan for addressing each area. Items are ordered roughly by impact and ease of diagnosis — start at Phase 1 and work down.

Covers Neoverse N1 (Graviton 2, Ampere Altra), N2 (Ampere Altra Max successor, Alibaba Yitian 710), V1 (Graviton 3), V2 (Graviton 4, NVIDIA Grace, Google Axion), and the upcoming N3/V3 generation.

---

## Architectural Comparison at a Glance

| Property | amd64 (x86_64) | aarch64 (Neoverse) |
|---|---|---|
| Default page size | 4 KB (fixed) | 4 KB (64 KB optional on some distros/kernels) |
| Huge page sizes | 2 MB, 1 GB | 2 MB, 1 GB (512 MB on some implementations) |
| L1D cache line size | 64 bytes | 64 bytes |
| L2 cache per core | 256 KB-2 MB (varies) | 1 MB (N1/N2), 2 MB (V2/N3/V3) |
| L3 / SLC | Shared, 1-4 MB/core | SLC via CMN mesh: implementation-defined (e.g. 36 MB/96 cores on Graviton 4, 32 MB/80 cores on Altra) |
| SIMD width | 128-bit SSE, 256-bit AVX2, 512-bit AVX-512 | 128-bit NEON (mandatory), SVE/SVE2 (128-2048 bit, length-agnostic) |
| SVE vector length | N/A | N1: no SVE; V1: 2x256b; N2: 2x128b SVE2; V2: 4x128b SVE2 |
| Memory ordering | TSO (strong — stores are totally ordered) | Weak (reordering across cores allowed) — DMB/DSB barriers needed |
| SMT depth | 2 threads/core (typical) | 1 thread/core (no SMT on Neoverse) |
| Atomics | LOCK prefix (CMPXCHG, XADD) | LSE atomics (CAS, SWP, LDADD) mandatory on ARMv8.1+ |
| Instruction encoding | Variable-length (1-15 bytes) | Fixed 32-bit words |
| Crypto acceleration | AES-NI, SHA extensions | AES, SHA-1/SHA-2, SHA-3 (V2+), PMULL (mandatory on ARMv8+) |

---

## Phase 1 — Compiler & Build System (Day 1)

These items produce immediate gains with minimal code changes.

### 1.1 Set `-mcpu=` correctly

The single highest-impact compiler change. Many aarch64 builds default to generic `-march=armv8-a`, missing substantial ISA improvements in newer cores.

- [ ] Identify current `-march` / `-mcpu` in all Makefiles, CMake, Meson configs
- [ ] Set `-mcpu=neoverse-n1` for Graviton 2 / Ampere Altra targets
- [ ] Set `-mcpu=neoverse-n2` for N2 targets (enables SVE2, BFloat16)
- [ ] Set `-mcpu=neoverse-v2` for Graviton 4 / Grace targets (enables SVE2, BFloat16, full LSE2)
- [ ] For DPDK: use meson cross-files or `-Dplatform=` options (e.g. `config/arm/arm64_neoverse_n2_linux_gcc`)
- [ ] Verify GCC version >= 10 for N2 support, >= 12 for V2 support, >= 14 for N3/V3
- [ ] Verify LLVM/Clang >= 15 for V2, >= 18 for N3/V3
- [ ] For portable builds targeting multiple Neoverse generations, use `-march=armv8.4-a+crypto+sve2`

### 1.2 Enable LSE atomics (critical)

LSE (Large System Extensions) atomics replace legacy `ldxr`/`stxr` exclusive loops with single instructions (`CAS`, `SWP`, `LDADD`). Under contention, LSE atomics are 10-50x faster than exclusive-pair loops on many-core systems.

- [ ] Verify LSE is enabled: `objdump -d binary | grep -c 'cas\|swp\|ldadd\|stadd'` should show LSE instructions
- [ ] If building with `-mcpu=neoverse-*`, LSE is enabled automatically (ARMv8.1+)
- [ ] For portable builds that must also run on ARMv8.0: use `-moutline-atomics` (GCC 10+, Clang 11+) — generates runtime dispatch between exclusive-pair and LSE paths
- [ ] Never use `-mno-lse` on server hardware — this is the single largest perf pitfall on aarch64
- [ ] In DPDK: `CONFIG_RTE_FORCE_INTRINSICS` should be off; verify meson picks up LSE correctly

### 1.3 Enable link-time optimization (LTO) and profile-guided optimization (PGO)

- [ ] Add `-flto` to CFLAGS/LDFLAGS
- [ ] Run a representative workload and generate PGO profile data with `-fprofile-generate`
- [ ] Rebuild with `-fprofile-use` against collected profiles
- [ ] Verify LTO doesn't regress build times unacceptably for development cycles

### 1.4 Audit third-party / vendored libraries

- [ ] Check whether crypto libraries (OpenSSL, IPP) are built with Neoverse-aware settings
- [ ] Verify `glibc` version >= 2.32 for optimized `memcpy`/`memset` with LSE and SVE
- [ ] Check that JIT engines (LuaJIT, V8) have aarch64 backends enabled and LSE-aware

---

## Phase 2 — Memory Model & Barriers (Week 1)

Memory ordering is where aarch64 diverges most from x86 assumptions.

### 2.1 Audit memory ordering assumptions

aarch64 has a **weak memory model** — unlike x86-TSO, stores from one core may be observed in different orders by different cores. Code that is "accidentally correct" on x86 may break or perform poorly on aarch64.

- [ ] Search for `volatile` used as a synchronization primitive — it does not provide ordering on aarch64
- [ ] Audit lock-free algorithms for missing barriers. On x86, store-release ordering is "free" (TSO); on aarch64, it requires a `DMB` instruction
- [ ] Replace hand-rolled barriers with C11/C++11 atomics (`<stdatomic.h>`, `<atomic>`) using appropriate `memory_order_*`
- [ ] Migrate `__sync_*` builtins to `__atomic_*` builtins — they respect the memory model
- [ ] Pay special attention to:
  - Seqlocks (readers must have acquire fences)
  - RCU-like patterns (publish/subscribe requires release/acquire)
  - Double-checked locking (needs acquire on the read side)
- [ ] Run `ThreadSanitizer` on aarch64 hardware to catch races that x86-TSO hides
- [ ] In DPDK: `rte_smp_rmb()` maps to `DMB ISHLD`, `rte_smp_wmb()` maps to `DMB ISHST` — verify these are present in hot paths, not full `DMB ISH`

### 2.2 Barrier cost awareness

- [ ] `DMB ISH` (full barrier) costs ~20-40 cycles on Neoverse, cheaper than POWER's `sync` but not free
- [ ] `DMB ISHLD` (load barrier) and `DMB ISHST` (store barrier) are cheaper — use the weakest barrier that is correct
- [ ] Prefer `LDAR`/`STLR` (acquire/release) over explicit `DMB` where possible — the hardware can optimize them better
- [ ] `ISB` (instruction barrier) is needed after system register writes, not for data ordering
- [ ] Audit for unnecessary full barriers — DPDK's ring library was optimized for aarch64 by replacing `__sync` with `__atomic` and weaker barriers

---

## Phase 3 — Cache & Memory (Week 1-2)

### 3.1 Cache line size (64 bytes — same as x86)

Unlike POWER (128-byte lines), aarch64 uses 64-byte cache lines. Most x86 cache-line-aligned code works without changes.

- [ ] Verify `RTE_CACHE_LINE_SIZE` is 64 in your DPDK build (it should be automatic on aarch64)
- [ ] Still audit for false sharing — 64-byte alignment is necessary but per-core L2 caches on Neoverse are private, making cross-core sharing patterns visible with `perf c2c`
- [ ] Struct padding/alignment from x86 code generally transfers correctly

### 3.2 Prefetch

- [ ] Replace `_mm_prefetch()` with `__builtin_prefetch()` (maps to `PRFM` instruction)
- [ ] `__builtin_prefetch(addr, 0, 3)` = prefetch for read into L1 (`PRFM PLDL1KEEP`)
- [ ] `__builtin_prefetch(addr, 1, 3)` = prefetch for write into L1 (`PRFM PSTL1KEEP`)
- [ ] Prefetch distances may need tuning: Neoverse N1/N2 have aggressive hardware prefetchers; manual prefetch can interfere
- [ ] V2 has enhanced hardware prefetchers — measure before adding manual prefetch, it may hurt
- [ ] For DPDK: `rte_prefetch0()` / `rte_prefetch1()` / `rte_prefetch2()` map correctly on aarch64

### 3.3 Huge pages

- [ ] Enable 2 MB huge pages: `echo N > /sys/kernel/mm/hugepages/hugepages-2048kB/nr_hugepages`
- [ ] For large buffer pools, use 1 GB huge pages (boot with `hugepagesz=1G hugepages=N`)
- [ ] DPDK EAL: use `--huge-dir` and `--socket-mem` for explicit control
- [ ] Profile TLB miss rate: `perf stat -e dTLB-load-misses,iTLB-load-misses`
- [ ] N1 TLB: 48-entry L1 DTLB, 1024-entry L2 TLB; V2: larger TLBs with improved walk latency

### 3.4 Cache maintenance awareness

- [ ] `DC CIVAC` (clean + invalidate by VA to PoC) is needed for DMA coherence on some platforms
- [ ] Most server SoCs (Graviton, Grace, Altra) are fully cache-coherent for DMA — explicit cache ops are not needed for normal DPDK PMDs
- [ ] MTE (Memory Tagging Extension, available on V2+) adds ~1-3% overhead when enabled — disable for latency-sensitive DPDK workloads unless debugging

---

## Phase 4 — SIMD & Vectorization (Week 2)

### 4.1 NEON intrinsics (128-bit, mandatory)

NEON is the baseline SIMD on all aarch64 cores. It operates on 128-bit vectors, equivalent to SSE.

- [ ] Inventory all `#include <immintrin.h>`, `<xmmintrin.h>`, `<emmintrin.h>` usage
- [ ] Evaluate porting strategy per hot path:
  - **Option A**: Use `<arm_neon.h>` intrinsics directly for critical paths
  - **Option B**: Use a portability layer like `simde` (SIMDe) or Highway (`google/highway`)
  - **Option C**: Use compiler auto-vectorization (`-O3 -ftree-vectorize`) and verify with `-fopt-info-vec-all`
- [ ] NEON maps 1:1 to SSE for 128-bit operations; 256-bit AVX2 code needs two NEON operations
- [ ] For AVX-512 code: significant restructuring needed — consider SVE2 if targeting V2+

### 4.2 SVE/SVE2 (variable-length, generation-dependent)

SVE is a length-agnostic SIMD ISA — code written for SVE works across different vector lengths without recompilation.

- [ ] N1: no SVE support (NEON only)
- [ ] V1 (Graviton 3): SVE at 256-bit (2x256b)
- [ ] N2: SVE2 at 128-bit (2x128b) — same width as NEON but with richer instruction set
- [ ] V2 (Graviton 4, Grace): SVE2 at 128-bit (4x128b pipelines) — wider execution but same register length
- [ ] N3/V3: SVE2, vector lengths TBD by implementation (expected 128-bit minimum)
- [ ] Use `-march=armv9-a+sve2` or `-mcpu=neoverse-v2` to enable SVE2 codegen
- [ ] SVE2 adds gather/scatter, complex multiply, bitwise permutations — useful for crypto and packet parsing
- [ ] For DPDK: LPM lookup has SVE-optimized path (`rte_lpm_lookupx4` → SVE variant)
- [ ] Verify auto-vectorization: `objdump -d | grep -c 'ld1\|st1\|add.*z[0-9]'` for SVE instructions

### 4.3 Crypto extensions

Hardware crypto is mandatory on ARMv8+ server cores and critical for IPsec/crypto PMDs.

- [ ] AES: use `<arm_neon.h>` AESE/AESD/AESMC/AESIMC intrinsics or let OpenSSL use them
- [ ] SHA: SHA-1 and SHA-256 hardware instructions available on all Neoverse
- [ ] SHA-3: available on V2+ (ARMv8.4-A)
- [ ] PMULL: polynomial multiply for GCM/GHASH — critical for AES-GCM performance
- [ ] In DPDK: `librte_crypto_armv8` and `librte_crypto_openssl` can leverage these
- [ ] Verify with: `cat /proc/cpuinfo | grep -o 'aes\|sha1\|sha2\|pmull\|sve2'`

---

## Phase 5 — Atomics & Synchronization (Week 2-3)

### 5.1 LSE atomic operations

LSE replaces the legacy exclusive monitor loop (`ldxr`/`stxr`) with single atomic instructions. This is the most important aarch64 optimization for multi-threaded DPDK workloads.

- [ ] `CAS` (compare-and-swap): used by `rte_atomic_compare_exchange`, ring enqueue/dequeue
- [ ] `SWP` (swap): used by spinlock acquire patterns
- [ ] `LDADD` / `STADD` (atomic add): used by counters, reference counts
- [ ] `LDSET` / `LDCLR`: atomic bit set/clear — useful for bitmask operations
- [ ] Under contention with 64+ cores, exclusive-pair loops cause severe cacheline bouncing; LSE instructions resolve this in the cache controller without retry loops
- [ ] LSE2 (ARMv8.4-A, available on V2+) adds naturally-aligned 128-bit atomics — relevant for DPDK's 128-bit CAS on descriptor rings
- [ ] Verify: `readelf -A binary | grep -i 'Tag_Feature_BTI\|atomics'` or check `/proc/cpuinfo` for `atomics`

### 5.2 Wait-for-event (WFE/SEVL)

- [ ] aarch64 provides `WFE` (wait-for-event) to put a core into low-power state while spinning
- [ ] DPDK supports `RTE_ARM_USE_WFE` — enable for power-sensitive deployments
- [ ] `WFE` reduces power and interconnect traffic during spinlock/ring contention
- [ ] On Neoverse, `WFE` typically pauses for ~100 ns or until an event (cache line write)
- [ ] Trade-off: `WFE` adds latency to lock acquisition — do not enable for sub-microsecond latency-critical paths

### 5.3 DPDK ring operations

- [ ] DPDK ring uses `__atomic_compare_exchange_n` with `__ATOMIC_ACQUIRE`/`__ATOMIC_RELEASE` on aarch64
- [ ] Verify ring performance with `ring_perf_autotest` — should show close to x86 performance with LSE enabled
- [ ] If ring throughput is poor, check for fallback to `ldxr`/`stxr` (indicates LSE not enabled at compile time)

---

## Phase 6 — Platform-Specific Tuning (Week 3)

### 6.1 NUMA topology

Most Neoverse server SoCs are single-socket, but multi-die or multi-socket configurations exist.

- [ ] Ampere Altra: single socket, single die, up to 80 cores — single NUMA node
- [ ] Ampere Altra Max: single socket, up to 128 cores — single NUMA node
- [ ] NVIDIA Grace Superchip: two dies connected via NVLink-C2C — presents as 2 NUMA nodes
- [ ] AWS Graviton 4: single socket (96 cores), but large instances span 2 sockets (192 vCPUs, 2 NUMA nodes)
- [ ] Map topology: `numactl --hardware` or `lscpu`
- [ ] For DPDK: ensure EAL `--socket-mem` and lcore pinning stay within a single NUMA node
- [ ] Pin NIC IRQs to the same NUMA node as DPDK lcores

### 6.2 CPU isolation and frequency

- [ ] Isolate DPDK cores: `isolcpus=` kernel parameter or `cset shield`
- [ ] Neoverse cores do not have SMT — each lcore maps to a physical core (no sibling contention)
- [ ] Set CPU governor to `performance`: `cpupower frequency-set -g performance`
- [ ] Neoverse cores may still have frequency scaling per-core — verify all DPDK cores run at max frequency
- [ ] Disable turbo/boost if consistent latency is more important than peak throughput

### 6.3 Kernel configuration

- [ ] Use 4 KB base pages (default on most aarch64 distros); some distros offer 16 KB or 64 KB page kernels
- [ ] 64 KB page kernels reduce TLB misses but waste memory for small allocations — test both if available
- [ ] Verify `CONFIG_ARM64_VA_BITS=48` or `52` for large address spaces
- [ ] For Grace: enable `CONFIG_NUMA=y` and verify NUMA balancing is disabled for DPDK cores (`echo 0 > /proc/sys/kernel/numa_balancing`)

---

## Phase 7 — Profiling & Validation (Ongoing)

### 7.1 Profiling tools

- [ ] `perf record -g` + `perf report` — basic CPU profiling
- [ ] `perf stat -d` — hardware counter summary (IPC, cache misses, branch mispredictions)
- [ ] `perf c2c` — false sharing analysis
- [ ] `perf stat -e dTLB-load-misses,iTLB-load-misses` — TLB pressure
- [ ] `objdump -d binary | grep -c 'dmb\|dsb\|isb'` — barrier frequency
- [ ] `objdump -d binary | grep -c 'cas\|swp\|ldadd'` — confirm LSE atomics in use
- [ ] `perf annotate` — per-instruction cycle attribution
- [ ] For DPDK: `dpdk-test-flow-perf`, `dpdk-testpmd` with `--stats-period`

### 7.2 Regression testing matrix

- [ ] Correctness: run full test suite under TSan on aarch64 hardware (catches memory model bugs)
- [ ] Performance: compare N1 vs N2 vs V2 if available — architectural differences can shift bottlenecks
- [ ] Atomics: verify LSE is active with `objdump` — a single missed `-march` flag can silently fall back to exclusive pairs
- [ ] NUMA: test with memory bound to local vs. remote node (Grace Superchip, multi-socket Graviton 4)
- [ ] Power: monitor per-core frequency during test to catch thermal throttling

---

## Quick Reference: Key Numbers

| Metric | x86_64 | N1 | N2 | V2 | N3 |
|---|---|---|---|---|---|
| Cache line | 64 B | 64 B | 64 B | 64 B | 64 B |
| Base page | 4 KB | 4 KB | 4 KB | 4 KB | 4 KB |
| L1D size | 32-48 KB | 64 KB | 64 KB | 64 KB | 64 KB |
| L1D latency | 4-5 cycles | 4 cycles | 4 cycles | ~4 cycles | ~4 cycles |
| L2 size/core | 256 KB-2 MB | 1 MB | 1 MB | 2 MB | 2 MB |
| L2 latency | ~12 cycles | ~10 cycles | ~10 cycles | ~9 cycles | ~9 cycles |
| L3 / SLC | 1-4 MB/core shared | 32 MB shared (Altra 80c) | implementation-defined | 36 MB SLC (Graviton 4, 96c) | implementation-defined |
| SMT | 2 | 1 (none) | 1 (none) | 1 (none) | 1 (none) |
| SIMD (fixed) | SSE 128b, AVX2 256b | NEON 128b | NEON 128b | NEON 128b | NEON 128b |
| SIMD (scalable) | AVX-512 512b | none | SVE2 2x128b | SVE2 4x128b | SVE2 (TBD) |
| Atomics | LOCK CMPXCHG | LSE (ARMv8.2) | LSE (ARMv9.0) | LSE2 (ARMv9.0) | LSE2 (ARMv9.2) |
| DRAM BW/socket | ~100-200 GB/s | ~100 GB/s (DDR4) | ~200 GB/s (DDR5) | ~300 GB/s (DDR5, Grace) | ~400 GB/s (DDR5) |
| DMB ISH cost | N/A (mfence ~30-50 cy) | ~20-40 cycles | ~20-40 cycles | ~20-30 cycles | ~reduced |

---

## Appendix: Compiler Flag Quick Reference

```bash
# Neoverse N1 (Graviton 2, Ampere Altra)
CFLAGS="-O3 -mcpu=neoverse-n1 -flto"

# Neoverse N2 (Alibaba Yitian 710)
CFLAGS="-O3 -mcpu=neoverse-n2 -flto"

# Neoverse V2 (Graviton 4, NVIDIA Grace, Google Axion)
CFLAGS="-O3 -mcpu=neoverse-v2 -flto"

# Portable build targeting ARMv8.4+ with SVE2 and crypto
CFLAGS="-O3 -march=armv8.4-a+crypto+sve2 -moutline-atomics -flto"

# Diagnostic flags
CFLAGS+=" -fopt-info-vec-missed"     # Report failed vectorization
CFLAGS+=" -fopt-info-loop-all"       # Loop optimization report

# Verify LSE atomics in output
objdump -d binary | grep -cE 'cas[ablp]?|swp[ablp]?|ldadd[ablp]?|stadd'
```
