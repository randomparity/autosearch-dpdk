# DPDK Project

Optimize DPDK packet processing throughput on bare-metal hardware. The agent
proposes source changes to a DPDK submodule, and the runner builds with
meson/ninja, deploys locally, runs testpmd (memif or PCI) or DTS, and reports
throughput in millions of packets per second (Mpps).

## Switching to this project

If another project is currently active, switch to DPDK and select a sprint:

```bash
uv run autoforge project switch dpdk
uv run autoforge sprint switch <sprint-name>   # e.g. 2026-03-26-memif-ppc64le
```

To see available sprints:

```bash
uv run autoforge sprint list
```

## Prerequisites

**Runner (lab machine):**

- Linux with hugepages configured
- meson, ninja, gcc, pkg-config, libnuma-dev (DPDK build dependencies)
- NIC ports (back-to-back) or memif vdevs (no hardware needed)
- Linux perf (optional, for profiling)
- Python 3.13+, [uv](https://docs.astral.sh/uv/)
- Passwordless sudo for testpmd and perf binaries (see [Sudo setup](#sudo-setup))

**Agent (workstation):**

- Python 3.13+, uv

## Runner setup (lab machine)

Clone the repo and install dependencies:

```bash
git clone --recurse-submodules <repo-url> autoforge
cd autoforge
uv sync
```

Create `.local.toml` overrides for system-specific settings. Shared defaults
are tracked in git; only override what differs on your machine:

```bash
# Override build directory for your system
cat > projects/dpdk/runner.local.toml <<'EOF'
[paths]
build_dir = "/fast-ssd/dpdk-build"
EOF

# Override lcores and port config for your hardware
cat > projects/dpdk/tests/testpmd-memif.local.toml <<'EOF'
[testpmd]
lcores = "96-103"
EOF
```

At minimum, override:

- `runner.local.toml` — `build_dir` if `/tmp/dpdk-build` is not suitable
- `tests/testpmd-memif.local.toml` — `lcores` and port config (PCI or vdev) for your hardware

Start the runner:

```bash
uv run autoforge-runner
```

## Agent setup (workstation)

Initialize the submodule and create a sprint:

```bash
git submodule update --init projects/dpdk/repo
uv run autoforge sprint init 2026-03-26-my-sprint
uv run autoforge sprint switch 2026-03-26-my-sprint
```

Start optimizing:

```bash
uv run autoforge context          # show current state
uv run autoforge submit -d "baseline run"
uv run autoforge poll             # wait for results
uv run autoforge judge            # keep or revert
```

## Plugins

### Builder (`local`)

Compiles DPDK from source using meson and ninja.

| Config key | Default | Description |
|------------|---------|-------------|
| `jobs` | `0` | Parallel build jobs; 0 uses all available CPU cores |
| `cross_file` | `""` | Meson cross-file for cross-compiling; empty for native builds |
| `extra_meson_args` | `""` | Additional meson setup arguments passed verbatim |

### Deployer (`local`)

Bare-metal pass-through — no deployment step needed. No configuration required.

### Tester (`testpmd-memif`)

PTY-based testpmd execution with memif or PCI ports. Runs `--auto-start
--tx-first`, collects throughput from accumulated RX-packets over the
measurement window, and reports the median across repeat runs.

| Config key | Default | Description |
|------------|---------|-------------|
| `lcores` | `"4-7"` | EAL lcore mask (main lcore + forwarding cores) |
| `pci` | — | PCI addresses of NIC ports (e.g. `["01:00.0", "01:00.1"]`) |
| `vdev` | — | Virtual devices (e.g. memif pair); each string passed as `--vdev` |
| `no_pci` | `true` | Disable PCI bus scanning (set `true` when using vdevs) |
| `extra_eal_args` | `[]` | Additional EAL arguments (list of strings) |
| `nb_cores` | `2` | Number of forwarding cores (excluding main lcore) |
| `rxq` / `txq` | `4` | Queues per port |
| `rxd` / `txd` | `4096` | Descriptors per queue |
| `burst` | `128` | Burst size for rx/tx |
| `forward_mode` | `"io"` | Forward mode: `io`, `macswap`, `mac`, etc. |
| `warmup_seconds` | `5` | Seconds to wait after tx_first before resetting stats |
| `measure_seconds` | `10` | Seconds to collect throughput measurement |
| `repeat_count` | `1` | Measurement runs; median is reported (use 3-5 for sub-1% gain detection) |
| `sudo` | `true` | Run testpmd with sudo |

### Tester (`dts-mlx5`)

DTS (DPDK Test Suite) integration for mlx5 NICs. Requires DTS installed
separately with topology files in `config/nodes.yaml` and
`config/test_run.yaml`.

| Section | Config key | Default | Description |
|---------|------------|---------|-------------|
| `[paths]` | `dts_dir` | `"/opt/dts"` | Path to the DTS installation directory |
| `[test]` | `test_suites` | `["perf"]` | DTS test suite names to run |
| `[test]` | `perf` | `true` | Enable DTS performance mode |
| `[test]` | `metric_path` | `"throughput_mpps"` | Dot-separated path into DTS JSON results to extract the metric |

### Profiler (`perf-record`)

Captures Linux perf profiles during the measurement window. Records call
stacks and hardware counters for post-run analysis.

| Config key | Default | Description |
|------------|---------|-------------|
| `enabled` | `false` | Capture perf profiles during measurement |
| `frequency` | `99` | Sampling frequency in Hz (99 avoids aliasing with timer interrupts) |
| `sudo` | `true` | Run perf commands with sudo |

## Campaign config

Each sprint has a `campaign.toml` that configures the optimization campaign.
See `projects/dpdk/sprints/*/campaign.toml` for examples. Key settings:

- **Metric:** `throughput_mpps` (millions of packets per second)
- **Direction:** `maximize`
- **Threshold:** `0.01` Mpps — changes must improve throughput by at least this amount (absolute delta)
- **Scope:** restricts which source paths the agent may modify (e.g. `drivers/net/memif/`, `lib/eal/ppc/`)

## Sudo setup

testpmd and perf require elevated privileges. Configure passwordless sudo for
just the specific binaries:

```bash
sudo visudo -f /etc/sudoers.d/dpdk-testpmd
# Add: <user> ALL=(root) NOPASSWD: <build_dir>/app/dpdk-testpmd

sudo visudo -f /etc/sudoers.d/dpdk-perf
# Add: <user> ALL=(root) NOPASSWD: /usr/bin/perf
```

## See also

- [Agent guide](../../docs/agent.md) — sprint workflow, campaign config, CLI reference
- [Runner guide](../../docs/runner.md) — systemd service setup, troubleshooting, config resolution
- [Plugin SDK](../../docs/plugin-sdk.md) — authoring new build, deploy, test, and profiler plugins
