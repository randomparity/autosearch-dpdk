# vLLM Project

Optimize vLLM inference serving throughput using containerized builds on GPU
hardware. The agent proposes engine configuration changes or source patches,
and the runner builds a container, deploys it with GPU passthrough, runs
`vllm bench serve`, and reports output token throughput.

## Prerequisites

**Runner (GPU host):**

- NVIDIA driver >= 525 with CUDA 12.x
- Podman with [nvidia-container-toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)
  configured for CDI (`nvidia-ctk cdi generate`)
- Python 3.13+, [uv](https://docs.astral.sh/uv/)

**Agent (workstation):**

- Python 3.13+, uv

## Runner setup (GPU host)

Clone the repo and install dependencies:

```bash
git clone --recurse-submodules <repo-url> autoforge
cd autoforge
uv sync
```

Verify GPU passthrough works:

```bash
podman run --rm --device nvidia.com/gpu=all docker.io/nvidia/cuda:12.8.0-base-ubuntu24.04 nvidia-smi
```

Copy and configure plugin configs:

```bash
cp projects/vllm/runner.toml.example projects/vllm/runner.toml
cp projects/vllm/builds/container.toml.example projects/vllm/builds/container.toml
cp projects/vllm/deploys/podman-gpu.toml.example projects/vllm/deploys/podman-gpu.toml
cp projects/vllm/tests/bench-serving.toml.example projects/vllm/tests/bench-serving.toml
cp projects/vllm/perfs/nvidia-smi.toml.example projects/vllm/perfs/nvidia-smi.toml
```

Edit each `.toml` file for your environment. At minimum:

- `deploys/podman-gpu.toml` — set `model`, `hf_cache`, and `HF_TOKEN`
- `runner.toml` — set `source_dir` if using source builds

Pre-pull the model to avoid first-run delay:

```bash
huggingface-cli download Qwen/Qwen3-0.6B
```

Start the runner:

```bash
uv run autoforge-runner
```

## Agent setup (workstation)

Initialize a sprint and set the pointer:

```bash
uv run autoforge sprint init 2026-03-26-baseline
uv run autoforge sprint switch 2026-03-26-baseline
```

Start optimizing:

```bash
uv run autoforge context          # show current state
uv run autoforge submit -d "baseline run"
uv run autoforge poll             # wait for results
uv run autoforge judge            # keep or revert
```

Or use the interactive loop:

```bash
uv run autoforge-loop --dry-run
```

## Plugins

### Builder (`container`)

Builds a container image using Podman. Two modes:

| Config key | Default | Description |
|------------|---------|-------------|
| `mode` | `"prebuilt"` | `"prebuilt"` pulls `base_image`; `"source"` builds from local source |
| `base_image` | `docker.io/vllm/vllm-openai:latest` | Base image to pull or build from |
| `local_tag` | `localhost/vllm-bench:latest` | Local tag for the built image |

### Deployer (`podman-gpu`)

Starts the vLLM container with GPU passthrough and waits for the health endpoint.

| Config key | Default | Description |
|------------|---------|-------------|
| `model` | `Qwen/Qwen3-0.6B` | HuggingFace model ID |
| `port` | `8000` | Host port for the OpenAI-compatible API |
| `container_name` | `vllm-bench` | Podman container name |
| `hf_cache` | `/home/user/.cache/huggingface` | Host path to HuggingFace cache (bind-mounted) |
| `gpu_memory_utilization` | `0.90` | Fraction of GPU memory to use |
| `startup_timeout` | `300` | Seconds to wait for health check |
| `engine_args` | `[]` | Extra args passed after `--model` |
| `[deploy.env]` | — | Environment variables (e.g. `HF_TOKEN`, `VLLM_ATTENTION_BACKEND`) |

### Tester (`bench-serving`)

Runs `vllm bench serve` against the deployed container and parses output token
throughput from the results.

| Config key | Default | Description |
|------------|---------|-------------|
| `num_prompts` | `100` | Number of prompts to send |
| `dataset_name` | `"random"` | Dataset for benchmark input |
| `random_input_len` | `512` | Input token length (random dataset) |
| `random_output_len` | `256` | Output token length (random dataset) |
| `max_concurrency` | `64` | Maximum concurrent requests |
| `request_rate` | `"inf"` | Request rate (`"inf"` for closed-loop) |
| `result_dir` | `/tmp/vllm-bench` | Directory for benchmark output files |
| `bench_cmd` | `"vllm"` | Command or path to `benchmark_serving.py` |

### Profiler (`nvidia-smi`)

Polls `nvidia-smi` at a configurable interval during the test run, capturing
GPU utilization as a CSV time series.

| Config key | Default | Description |
|------------|---------|-------------|
| `interval_ms` | `500` | Polling interval in milliseconds |

## Campaign config

The baseline sprint config at
`projects/vllm/sprints/2026-03-26-baseline/campaign.toml` targets:

- **Metric:** `output_throughput_tok_s` (output tokens per second)
- **Direction:** `maximize`
- **Threshold:** `2.0%` — changes must improve throughput by at least 2%

## Optimization strategies

- **Engine args** — attention backend, chunked prefill, speculative decoding,
  max batch size, KV cache tuning
- **Environment variables** — `VLLM_ATTENTION_BACKEND`, CUDA tuning flags,
  tensor parallel configuration
- **Source patches** — scheduler changes, kernel optimizations, memory
  management improvements (requires `mode = "source"` in builder config)
