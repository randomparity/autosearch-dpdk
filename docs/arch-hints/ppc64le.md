# ppc64le Performance Optimization Checklist

## Porting from amd64 to POWER9 / Power10 / Power11

This checklist identifies the major architectural differences between amd64 (x86_64) and ppc64le that affect application performance, and provides an iterative plan for addressing each area. Items are ordered roughly by impact and ease of diagnosis — start at Phase 1 and work down.

---

## Architectural Comparison at a Glance

| Property | amd64 (x86_64) | ppc64le (POWER9/10/11) |
|---|---|---|
| Default page size | 4 KB (fixed) | 64 KB (configurable: 4 KB or 64 KB) |
| Huge page sizes | 2 MB, 1 GB | 16 MB, 16 GB |
| L1D cache line size | 64 bytes | 128 bytes |
| L2 cache line size | 64 bytes | 128 bytes |
| L2 cache per core | 256 KB–2 MB (varies) | 512 KB (P9), 2 MB (P10/P11) |
| L3 cache | Shared, 1–4 MB/core | 10 MB/core eDRAM (P9), 8 MB/core (P10) |
| SIMD width | 128-bit SSE, 256-bit AVX2, 512-bit AVX-512 | 128-bit VSX / AltiVec |
| Matrix acceleration | AMX (Sapphire Rapids+) | MMA — 512-bit accumulators (P10/P11) |
| Memory ordering | TSO (strong — stores are totally ordered) | Weak (reordering across cores allowed) |
| SMT depth | 2 threads/core (typical) | 4 or 8 threads/core (SMT4 / SMT8) |
| Long double format | 80-bit x87 extended | IEEE 128-bit binary128 (P9+) |
| Instruction encoding | Variable-length (1–15 bytes) | Fixed 32-bit words (prefix/fuse in ISA 3.1) |
| ABI stack linkage | 128-byte red zone, no mandatory linkage area | 32-byte minimum linkage area required |

---

## Phase 1 — Compiler & Build System (Day 1)

These items produce immediate gains with minimal code changes.

### 1.1 Set `-mcpu=` and `-mtune=` correctly

The single highest-impact compiler change. Many ppc64le builds default to `-mcpu=power8`, missing substantial ISA improvements in P9/P10/P11.

- [ ] Identify current `-mcpu` / `-mtune` in all Makefiles, CMake, Meson, spec files
- [ ] Set `-mcpu=power9` minimum for POWER9 targets
- [ ] Set `-mcpu=power10` for Power10/P11 targets (enables MMA, prefixed instructions, `pcrel`)
- [ ] Verify GCC version ≥ 10 (for Power10 MMA built-ins) or ≥ 14 (for Power11 tuning)
- [ ] Verify LLVM/Clang version ≥ 12 for Power10 MMA support
- [ ] Add `-mpower10-fusion` if available (enables instruction fusion)
- [ ] Check for stale `-march=` or `-mcpu=powerpc64le` generic fallbacks in upstream build systems

### 1.2 Enable link-time optimization (LTO) and profile-guided optimization (PGO)

- [ ] Add `-flto` to CFLAGS/LDFLAGS
- [ ] Run a representative workload and generate PGO profile data with `-fprofile-generate`
- [ ] Rebuild with `-fprofile-use` against collected profiles
- [ ] Verify LTO doesn't regress build times unacceptably for development cycles

### 1.3 Verify long double format

Fedora 36+ and current SLES/RHEL default to IEEE 128-bit long double (`binary128`) on ppc64le. Older builds may use IBM double-double format. Mixing the two in shared libraries causes silent corruption.

- [ ] Check `LDBL_MANT_DIG` — 113 = IEEE binary128, 106 = IBM double-double
- [ ] Ensure all linked libraries (OpenSSL, glibc, libstdc++) use the same format
- [ ] If building with Clang, add `-mabi=ieeelongdouble` explicitly if your distro defaults differ
- [ ] If floating-point precision is not critical, consider `-mno-float128` to avoid quad-precision overhead in hot paths

### 1.4 Audit third-party / vendored libraries

- [ ] Check whether OpenBLAS, FFTW, Eigen, etc. are built with Power10-aware settings (not falling back to generic PPC or Power8)
- [ ] Use `IBM ESSL` where available for BLAS/LAPACK — it has MMA-optimized kernels
- [ ] Verify `glibc` version is ≥ 2.28 for POWER9 `hwcap` support; ≥ 2.31 for optimized string/mem routines
- [ ] Check that any JIT engines (LuaJIT, V8, SpiderMonkey) have ppc64le backends enabled

---

## Phase 2 — Memory Subsystem & Page Size (Week 1)

Memory behavior is where ppc64le diverges most from x86 assumptions.

### 2.1 Page size awareness

ppc64le Linux typically uses 64 KB base pages (vs. 4 KB on x86_64). This has wide-ranging effects.

- [ ] Run `getconf PAGESIZE` to confirm running page size (65536 = 64 KB)
- [ ] Audit all code that hardcodes `4096`, `PAGE_SIZE`, or `1 << 12` — replace with `sysconf(_SC_PAGESIZE)` or `getpagesize()`
- [ ] Check `mmap()` alignment assumptions — regions must be 64 KB aligned
- [ ] Check custom allocators (jemalloc, tcmalloc, mimalloc) for page size assumptions in slab/arena sizing
- [ ] Evaluate memory waste: 64 KB pages waste more on small allocations. For many-small-allocation workloads, consider switching to a 4 KB page kernel if available
- [ ] If your workload is HPC/database/large-working-set, the 64 KB default is likely beneficial — verify with `perf stat -e dTLB-load-misses`

### 2.2 Huge pages / large pages

- [ ] Enable 16 MB huge pages: `echo N > /proc/sys/vm/nr_hugepages` (where N = number of pages)
- [ ] For databases and large buffers, use `mmap()` with `MAP_HUGETLB` or `madvise(MADV_HUGEPAGE)`
- [ ] Enable transparent huge pages (THP) if appropriate: `echo always > /sys/kernel/mm/transparent_hugepage/enabled`
- [ ] Profile TLB miss rate before/after: `perf stat -e dTLB-load-misses,iTLB-load-misses`
- [ ] For POWER9: TLB has 1024 entries (4-way), supporting 4 KB / 64 KB / 2 MB / 16 MB / 1 GB / 16 GB pages
- [ ] For Power10: TLB expanded to 4096 entries with reduced latency

### 2.3 Cache line size (128 bytes vs. 64 bytes)

This is the #1 source of false sharing regressions on POWER.

- [ ] Grep for `__cacheline_aligned`, `__attribute__((aligned(64)))`, or `#define CACHE_LINE_SIZE 64`
- [ ] Update all cache-line alignment macros to 128 bytes for ppc64le
- [ ] Audit lock-free data structures: per-thread counters, ring buffers, work-stealing queues — pad to 128 bytes between independent fields
- [ ] Run false-sharing detection: `perf c2c record -a` then `perf c2c report`
- [ ] In DPDK: verify `RTE_CACHE_LINE_SIZE` is 128 on your build (it should be automatic, but confirm)
- [ ] Review struct layout in hot paths — two "independent" fields that fit within 64 bytes on x86 may share a 128-byte cache line and contend on POWER

---

## Phase 3 — SIMD & Vectorization (Week 2)

### 3.1 Migrate x86 SIMD intrinsics

There is no hardware translation from SSE/AVX to VSX. All x86 intrinsics must be rewritten.

- [ ] Inventory all `#include <immintrin.h>`, `<xmmintrin.h>`, `<emmintrin.h>` usage
- [ ] Evaluate porting strategy per hot path:
  - **Option A**: Use compiler auto-vectorization (`-O3 -ftree-vectorize`) and verify with `-fopt-info-vec-all`
  - **Option B**: Use `<altivec.h>` / VSX intrinsics directly for critical paths
  - **Option C**: Use a portability layer like `simde` (SIMDe) or Highway (`google/highway`)
- [ ] Replace `_mm_prefetch()` with `__builtin_prefetch()` or `dcbt` intrinsics
- [ ] Note: VSX registers are 128-bit (like SSE), so 256-bit AVX2 code needs to be split into two VSX operations
- [ ] For AVX-512 code: significant restructuring needed — no direct equivalent exists

### 3.2 Leverage MMA (Power10 / Power11)

The Matrix Math Accelerator provides 512-bit accumulator registers and outer-product instructions — 4× improvement over POWER9 for dense linear algebra.

- [ ] Identify GEMM, convolution, FFT, and other matrix-heavy hot paths
- [ ] Link against MMA-optimized BLAS: OpenBLAS (≥ 0.3.21 with Power10 kernels) or IBM ESSL
- [ ] For custom kernels: use GCC/Clang MMA built-ins (`__builtin_mma_xvf64ger`, etc.)
- [ ] Supported MMA data types: FP64, FP32, BFloat16, FP16, INT16, INT8, INT4
- [ ] For AI inference: frameworks like PyTorch and TensorFlow on ppc64le already use MMA via OpenBLAS/ESSL — ensure you are using the Power10-optimized builds (RocketCE channel on Anaconda)

### 3.3 Verify auto-vectorization effectiveness

- [ ] Build with `-O3 -fopt-info-vec-missed` and review which loops failed to vectorize
- [ ] Common blocker: pointer aliasing — add `restrict` qualifiers where safe
- [ ] Common blocker: non-contiguous memory access patterns — restructure data layouts (AoS → SoA)
- [ ] Use `-maltivec -mvsx` explicitly if not implied by `-mcpu`
- [ ] Check `objdump -d` output for VSX instructions in hot functions (`lxv`, `stxv`, `xvmadd*`, `xxperm*`)

---

## Phase 4 — Memory Ordering & Concurrency (Week 2–3)

### 4.1 Audit memory ordering assumptions

POWER has a **weak memory model** — unlike x86-TSO, stores from one core may be observed in different orders by different cores. Code that is "accidentally correct" on x86 may break or perform poorly on POWER.

- [ ] Search for `volatile` used as a synchronization primitive — it does not provide ordering on POWER
- [ ] Audit lock-free algorithms for missing barriers. On x86, store-release ordering is "free" (TSO); on POWER, it requires an `lwsync` or `sync` instruction
- [ ] Replace hand-rolled barriers with C11/C++11 atomics (`<stdatomic.h>`, `<atomic>`) using the appropriate `memory_order_*`
- [ ] For existing uses of `__sync_*` GCC builtins, migrate to `__atomic_*` builtins which respect the memory model
- [ ] Pay special attention to:
  - Seqlocks (readers must have acquire fences on POWER)
  - RCU-like patterns (publish/subscribe requires release/acquire)
  - Double-checked locking (needs acquire on the read side)
- [ ] Run `ThreadSanitizer` on POWER hardware to catch races that x86-TSO hides
- [ ] Note: `sync` (heavyweight barrier) on POWER can cost 200–300 cycles. Use `lwsync` (lightweight sync) where full ordering is not required
- [ ] Prefer `isync` for control dependencies (e.g., after a branch on a loaded value)

### 4.2 NUMA topology

POWER9/P10/P11 systems are deeply NUMA. Each socket has its own memory controllers, and cross-socket latency is significant.

- [ ] Map your process topology with `numactl --hardware` or `lscpu`
- [ ] Bind latency-sensitive processes/threads to a single NUMA node: `numactl --cpunodebind=0 --membind=0`
- [ ] For multi-socket systems, verify memory allocation policy: `numactl --preferred` vs. `--interleave`
- [ ] Profile NUMA migration overhead: `perf stat -e numa-*` events
- [ ] For DPDK: ensure EAL arguments include `--socket-mem` and core pinning within a single NUMA node

---

## Phase 5 — SMT Tuning (Week 3)

### 5.1 Thread density

POWER9 supports SMT4 or SMT8; Power10/P11 support SMT8. This is dramatically different from x86's typical 2-way SMT.

- [ ] Profile at SMT1, SMT2, SMT4, and SMT8 to find the optimal thread count for your workload
- [ ] Latency-sensitive workloads (databases, packet processing) often perform best at SMT1 or SMT2
- [ ] Throughput-oriented workloads (batch processing, compilation) can benefit from SMT4/SMT8
- [ ] Control SMT level: `ppc64_cpu --smt=N` where N = 1, 2, 4, or 8
- [ ] Note: L1D cache in "private per thread" mode (Spectre mitigation on P9) adds ~12 cycles for cross-thread L1D access
- [ ] Re-tune thread pool sizes — applications tuned for "2× physical cores" on x86 need adjustment to "1–8× physical cores"

### 5.2 CPU frequency and power modes

- [ ] Verify governor: `cpupower frequency-info` — use `performance` for benchmarking
- [ ] On Power11: evaluate the new "high-efficiency mode" (underclocking for ~30% less power at ~10% perf cost) if power-constrained
- [ ] POWER chips boost aggressively in low-thread-count scenarios — SMT1 may run significantly faster per-thread than SMT8

---

## Phase 6 — Kernel & OS Tuning (Week 3–4)

### 6.1 Kernel configuration

- [ ] Verify Radix MMU is enabled (default on P9+ with Linux ≥ 4.15) — provides faster page walks than Hash Page Table (HPT)
- [ ] On P9: confirm `CONFIG_PPC_64K_PAGES=y` or `=n` matches your workload needs
- [ ] Verify `CONFIG_PPC_RADIX_MMU=y`
- [ ] For container workloads: ensure cgroup v2 is enabled and memory controller is active

### 6.2 Filesystem considerations

- [ ] If using Btrfs: sector size matches page size. A filesystem created on 64 KB pages cannot be mounted on a 4 KB page kernel
- [ ] ext4 and XFS work correctly with both page sizes
- [ ] For NVMe: verify queue depth and I/O scheduler settings (`mq-deadline` or `none` for NVMe)

### 6.3 Crypto & compression acceleration

POWER9/P10/P11 have on-chip accelerators for AES, SHA, random number generation, and compression.

- [ ] Verify OpenSSL is using hardware acceleration: `openssl speed -evp aes-256-gcm` should show high throughput
- [ ] For Power10+: hardware AES and SHA-3 engines are available — ensure your crypto library enables them
- [ ] Power10/P11: on-chip compression acceleration (842 compression) — check if your workload can leverage it via `nx-gzip`

---

## Phase 7 — Profiling & Validation (Ongoing)

### 7.1 Profiling tools

- [ ] `perf record -g` + `perf report` — basic CPU profiling
- [ ] `perf stat -d` — hardware counter summary (IPC, cache misses, branch mispredictions)
- [ ] `perf c2c` — false sharing analysis (critical for 128-byte cache lines)
- [ ] `perf stat -e dTLB-load-misses,iTLB-load-misses` — TLB pressure
- [ ] `opcodes` analysis: `objdump -d binary | grep -c 'sync\|lwsync\|isync'` — barrier frequency
- [ ] `oprofile` or `perf annotate` — per-instruction cycle attribution
- [ ] For DPDK: use `dpdk-test-flow-perf`, `dpdk-testpmd` with `--stats-period`

### 7.2 Regression testing matrix

- [ ] Correctness: run full test suite under TSan on POWER hardware (catches memory model bugs)
- [ ] Performance: benchmark at SMT1, SMT2, SMT4, SMT8
- [ ] Memory: run under Valgrind or ASan to catch alignment issues
- [ ] Page size: test on both 4 KB and 64 KB page kernels if your distro supports both
- [ ] NUMA: test with memory bound to local vs. remote node

---

## Quick Reference: Key Numbers

| Metric | x86_64 | POWER9 | Power10 | Power11 |
|---|---|---|---|---|
| Cache line | 64 B | 128 B | 128 B | 128 B |
| Base page | 4 KB | 64 KB (or 4 KB) | 64 KB (or 4 KB) | 64 KB (or 4 KB) |
| L1D latency | 4–5 cycles | 4 cycles | ~4 cycles | ~4 cycles |
| L2 latency | ~12 cycles | ~12 cycles | ~10 cycles | ~10 cycles |
| L2 size/core | 256 KB–2 MB | 512 KB | 2 MB | 2 MB |
| L3 size/core | 1–4 MB | 10 MB (eDRAM) | 8 MB (eDRAM) | ~8 MB |
| Max SMT | 2 | 4 or 8 | 8 | 8 |
| VSX width | N/A (SSE=128, AVX=256) | 128-bit | 128-bit | 128-bit |
| MMA accumulators | N/A | N/A | 8 × 512-bit | 8 × 512-bit (enhanced) |
| DRAM bandwidth/socket | ~100–200 GB/s | ~120 GB/s (DDR4) | ~410 GB/s (DDR5 OMI) | >410 GB/s (DDR5 OMI) |
| `sync` cost | N/A (`mfence` ~30–50 cy) | ~200–300 cycles | ~reduced | ~reduced |
| `lwsync` cost | N/A | ~40–60 cycles | ~reduced | ~reduced |

---

## Appendix: Compiler Flag Quick Reference

```bash
# POWER9 optimized build
CFLAGS="-O3 -mcpu=power9 -mtune=power9 -mvsx -maltivec -flto"

# Power10 optimized build (enables MMA, pcrel, prefixed insns)
CFLAGS="-O3 -mcpu=power10 -mtune=power10 -mvsx -maltivec -mmma -flto"

# Power11 optimized build (GCC 14+)
CFLAGS="-O3 -mcpu=power11 -mtune=power11 -mvsx -maltivec -mmma -flto"

# Diagnostic flags
CFLAGS+=" -fopt-info-vec-missed"     # Report failed vectorization
CFLAGS+=" -fopt-info-loop-all"       # Loop optimization report
CFLAGS+=" -Wpsabi"                   # Warn about ABI differences
```
