# Sprint Results: 2026-03-27-vllm-perf-profiling

## Objective

Optimize Python-level serving throughput for Qwen3-0.6B on vLLM, guided by Linux perf CPU profiling.

## Outcome

**No meaningful throughput improvement achieved.** After 18 experiments across multiple optimization strategies, the sprint found that Python overhead is not the bottleneck for this workload. GPU compute dominates the per-step time.

## Key Numbers

| Metric | Value |
|--------|-------|
| Baseline (5-iter mean, warmup excluded) | 4987 tok/s |
| Best experimental result | 5005 tok/s (+0.4%) |
| Finale | 5002 tok/s (+0.3%) |
| Iterations used | 18 / 50 |
| Measurement noise floor | ~0.5% (5-iteration mean) |

Note: Early experiments used a single benchmark iteration with 100 prompts, producing ~1% noise. Mid-sprint we switched to 5 iterations with 200 prompts and warmup exclusion, reducing noise to ~0.5%.

## What Was Tried

### Python micro-optimizations (no measurable effect)

1. **check_stop fast path** — removed asserts, cached `num_output_tokens`, short-circuited stop_token_ids check, single-token decode fast path
1. **Context manager removal** — removed `record_function_or_nullcontext` wrapping `allocate_slots` in the per-request scheduler loop (100 nullcontext enter/exit per step)
1. **Local variable caching** — cached `self.requests`, `req_id_to_index` as locals in `update_from_output` and `_update_after_schedule`
1. **Inline token append** — bypassed `isinstance` check and method dispatch in single-token decode path
1. **Block hash skip** — avoided `list.extend([])` in `update_block_hashes` when no new blocks
1. **GC threshold tuning** — raised gen-0 threshold from 700 to 50000 allocations

### Numpy fast path (regressed)

1. **Decode fast path in `_prepare_inputs`** — attempted to skip `np.repeat`, `cumsum`, and batched-arange for pure decode. Regressed due to dtype mismatches (int64 vs int32) and array view aliasing.

### Configuration changes (no measurable effect)

1. **`max_num_batched_tokens` 8192 → 16384** — no impact with 64 max concurrency
1. **`max_num_seqs` 256 → 512** — no impact with 64 max concurrency
1. **`stream_interval` 1 → 4** — batches output processing; no effect on engine core throughput

## Why Nothing Worked

### CPU profile data (perf, 99Hz, 30s capture)

| Function | Samples | % |
|----------|---------|---|
| [unknown] (CUDA/driver) | 51 | 39% |
| _PyEval_EvalFrameDefault | 22 | 17% |
| tokenizers BPE merge | 5-7 | 4-5% |
| malloc/cfree | 5-8 | 4-6% |
| PyObject_GC_Del | 3 | 2% |
| _PyObject_GenericGetAttrWithDict | 3 | 2% |

**Total samples: ~130.** The low sample count (profiling the host-side PID of a Docker container at 99Hz) makes percentages noisy, but the direction is clear.

### Analysis

- **39% of CPU time is in unknown symbols** — CUDA driver, GPU kernel launches, and JIT code. Python has zero visibility into this.
- **17% Python interpreter** is distributed across thousands of small operations. No single function dominates enough to optimize meaningfully.
- **TPOT ~11.7ms** per step for 64 concurrent requests. With Qwen3-0.6B (0.6B params), the GPU forward pass for batch-64 likely takes 8-10ms. Python overhead is ~2-3ms, and our optimizations saved <0.1ms.
- **CUDA graphs** are enabled and capturing decode steps, minimizing kernel launch overhead.
- **The benchmark is GPU-bound**, not Python-bound. To make Python optimizations measurable, the workload would need higher concurrency (more requests per step → more Python scheduling work per step) or a CPU-bound serving configuration.

## Infrastructure Improvements

The sprint produced several improvements to the autoforge framework:

1. **Multi-iteration benchmarking** — test plugin now supports `iterations` config, runs N iterations, excludes warmup, reports mean of remaining runs
2. **Warmup exclusion** — first iteration (model load, JIT, cache priming) is automatically excluded from averaging
3. **Perf profiling for containers** — fixed PEBS event compatibility (`-e cycles` explicit), sudo support for cross-container profiling, documented `perf_event_paranoid` requirements
4. **Profiler skip for finale** — fixed bug where runner ignored request's empty `profile_plugin` field
5. **Ramdisk builds** — moved build directory to `/mnt/ramdisk` for faster container builds

## Lessons Learned

1. **Profile before optimizing.** Initial experiments were wasted on Python micro-optimizations before confirming Python was the bottleneck. The perf profile (once working) immediately showed GPU/driver as the dominant cost.
2. **Measurement precision matters.** Single-run benchmarks had ~1% noise, making it impossible to distinguish real 0.5% improvements from noise. The 5-iteration mean reduced noise to ~0.5%.
3. **Small models are GPU-bound even with batch overhead.** Despite Qwen3-0.6B being tiny, the GPU forward pass at batch-64 still dominates. Python overhead only matters at much higher concurrency or with CPU-bound serving patterns.
4. **Validate tooling first.** Three experiments ran before discovering perf wasn't working (missing binary, PEBS compatibility, container security restrictions).

## Recommendations for Future Sprints

- **Higher concurrency benchmark** — use `max_concurrency=256+` to shift the bottleneck toward Python scheduling
- **Larger models** — 7B+ models have longer GPU forward passes, making Python overhead proportionally smaller. Focus on models where batch sizes are constrained by memory.
- **CPU-bound workloads** — target workloads with high request churn (short prompts, short outputs) where scheduling overhead dominates
- **GPU-side profiling** — use NVIDIA Nsight Systems instead of Linux perf to profile GPU kernel execution and identify idle gaps

## System Info

_Sysinfo collected from the agent workstation. Runner sysinfo was not captured
correctly (`sysinfo --role runner` ran locally). The runner is a separate Linux
x86_64 machine with an NVIDIA GPU, accessed only via git._

| Property | Agent (local) |
| --- | --- |
| Hostname | Maximus.local |
| OS | Darwin 25.4.0 |
| Architecture | arm64 |
| CPU | Apple M5 Max |
| Physical cores | 18 |
| Memory (GB) | 128.0 |
| Python | 3.14.3 |

| Property | Runner (inferred from logs) |
| --- | --- |
| Hostname | homer |
| OS | Linux x86_64 |
| CPU | unknown (18+ cores) |
| Memory (GB) | 252 (126 GB per NUMA node) |
| GPU | NVIDIA (model unknown, supports CUDA graphs) |
| Ramdisk | 96 GB tmpfs at /mnt/ramdisk |
