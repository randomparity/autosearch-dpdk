# vLLM Project

Optimize vLLM inference serving throughput using containerized builds on GPU
hardware. The agent proposes engine configuration changes or source patches,
and the runner builds a container, deploys it with GPU passthrough, runs
`vllm bench serve`, and reports output token throughput.

## Switching to this project

If another project is currently active, switch to vLLM and select a sprint:

```bash
uv run autoforge project switch vllm
uv run autoforge sprint switch <sprint-name>   # e.g. 2026-03-26-baseline
```

To see available sprints:

```bash
uv run autoforge sprint list
```

## Prerequisites

**Runner (GPU host):**

- NVIDIA driver >= 525 with CUDA 12.x
- Docker or Podman with [nvidia-container-toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)
  - Docker: install the toolkit and restart the Docker daemon
  - Podman: configure CDI (`nvidia-ctk cdi generate`)
- Python 3.13+, [uv](https://docs.astral.sh/uv/)

**Agent (workstation):**

- Python 3.13+, uv, huggingface-cli

## Agent setup (workstation)

Switch to the vLLM project and initialize a sprint:

```bash
uv run autoforge project switch vllm
uv run autoforge sprint init 2026-03-26-baseline
```

Commit and push the pointer update so the runner can pull it:

```bash
git add .autoforge.toml
git commit -m "chore: switch to vllm/2026-03-26-baseline"
git push
```

Verify the configuration is complete:

```bash
uv run autoforge doctor
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

## Runner setup (GPU host)

> Complete agent setup first — the agent commits `.autoforge.toml` which the
> runner reads at startup.

Clone the repo and install dependencies:

```bash
git clone --recurse-submodules <repo-url> autoforge
cd autoforge
uv sync
```

The active project and sprint are set by the agent and committed to
`.autoforge.toml`. Clone the repo to pick them up automatically.

Verify GPU passthrough works:

```bash
# Docker
docker run --rm --gpus all docker.io/nvidia/cuda:12.8.0-base-ubuntu24.04 nvidia-smi

# Podman
podman run --rm --device nvidia.com/gpu=all docker.io/nvidia/cuda:12.8.0-base-ubuntu24.04 nvidia-smi
```

Create `.local.toml` overrides for system-specific settings. Shared defaults
are tracked in git; only override what differs on your machine:

```bash
# Override HuggingFace cache path and GPU devices for your system
cat > projects/vllm/deploys/container-gpu.local.toml <<'EOF'
[deploy]
hf_cache = "/data/hf-cache"
devices = "0"
EOF
```

Set the `HF_TOKEN` environment variable for model downloads:

```bash
export HF_TOKEN="hf_..."   # or add to ~/.bashrc
```

The tracked `container-gpu.toml` uses `${HF_TOKEN:-}` which resolves from
your environment at runtime — no need to put secrets in config files.

Verify the configuration file is complete, investigate any warning/fail messages displayed.

```bash
uv run autoforge doctor --role runner
```

Pre-pull the model to avoid first-run delay:

```bash
huggingface-cli download Qwen/Qwen3-0.6B
```

Start the runner:

```bash
uv run autoforge-runner
```

## Plugins

### Builder (`container`)

Builds a container image using Docker or Podman. Two modes:

| Config key | Default | Description |
|------------|---------|-------------|
| `runtime` | `"auto"` | `"auto"` detects Docker then Podman; or set `"docker"` / `"podman"` |
| `mode` | `"prebuilt"` | `"prebuilt"` pulls `base_image`; `"source"` builds from local source |
| `base_image` | `docker.io/vllm/vllm-openai:latest` | Base image to pull or build from |
| `local_tag` | `localhost/vllm-bench:latest` | Local tag for the built image |

### Deployer (`container-gpu`)

Starts the vLLM container with GPU passthrough and waits for the health endpoint.
Automatically selects the correct GPU flags: `--gpus all` for Docker,
`--device nvidia.com/gpu=all` for Podman.

| Config key | Default | Description |
|------------|---------|-------------|
| `runtime` | `"auto"` | `"auto"` detects Docker then Podman; or set `"docker"` / `"podman"` |
| `model` | `Qwen/Qwen3-0.6B` | HuggingFace model ID |
| `port` | `8000` | Host port for the OpenAI-compatible API |
| `container_name` | `vllm-bench` | Container name |
| `hf_cache` | `$HOME/.cache/huggingface` | Host path to HuggingFace cache (bind-mounted; resolved at runtime from `Path.home()`) |
| `gpu_memory_utilization` | `0.90` | Fraction of GPU memory to use |
| `startup_timeout` | `300` | Seconds to wait for health check |
| `devices` | `"all"` | GPUs to expose: `"all"` or comma-separated indices e.g. `"0"` or `"0,1"` |
| `engine_args` | `[]` | Extra args passed after `--model` |
| `[deploy.env]` | — | Environment variables (e.g. `HF_TOKEN`, `VLLM_ATTENTION_BACKEND`) |

### Tester (`bench-serving`)

Runs `vllm bench serve` inside the deployed container via `docker exec` /
`podman exec` and parses output token throughput from the results.

Config section: `[bench]`

| Config key | Default | Description |
|------------|---------|-------------|
| `num_prompts` | `100` | Number of prompts to send |
| `dataset_name` | `"random"` | Dataset for benchmark input |
| `random_input_len` | `512` | Input token length (random dataset) |
| `random_output_len` | `256` | Output token length (random dataset) |
| `max_concurrency` | `64` | Maximum concurrent requests |
| `request_rate` | `"inf"` | Request rate (`"inf"` for closed-loop) |

### Profiler (`nvidia-smi`)

Polls `nvidia-smi` at a configurable interval during the test run, capturing
GPU utilization as a CSV time series.

| Config key | Default | Description |
|------------|---------|-------------|
| `interval_ms` | `500` | Polling interval in milliseconds |

## Campaign config

The baseline sprint config at
`projects/vllm/sprints/2026-03-26-baseline/campaign.toml` targets:

- **Metric:** `output_throughput` (output tokens per second)
- **Direction:** `maximize`
- **Threshold:** `2.0` tok/s — changes must improve throughput by at least this amount (absolute delta)

## Optimization strategies

- **Engine args** — attention backend, chunked prefill, speculative decoding,
  max batch size, KV cache tuning
- **Environment variables** — `VLLM_ATTENTION_BACKEND`, CUDA tuning flags,
  tensor parallel configuration
- **Source patches** — scheduler changes, kernel optimizations, memory
  management improvements (requires `mode = "source"` in builder config)

## See also

- [Agent guide](../../docs/agent.md) — sprint workflow, campaign config, CLI reference
- [Runner guide](../../docs/runner.md) — systemd service setup, troubleshooting
- [Plugin SDK](../../docs/plugin-sdk.md) — authoring new plugins
