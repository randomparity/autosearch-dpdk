# Runner Guide

The runner runs on a lab machine with your project's target hardware. It polls
for test requests, builds the project, runs performance tests, and pushes
results back via git. Examples below use the DPDK project — see each project's
README for project-specific setup (e.g. `projects/vllm/README.md`).

## Prerequisites

- Python 3.13+
- [uv](https://docs.astral.sh/uv/)
- Project-specific build dependencies (e.g. meson, ninja, gcc for DPDK; Docker/Podman for vLLM)
- Git access to the autoforge repository (push permissions)
- For testpmd plugin: NIC ports connected back-to-back (or memif vdevs)
- For DTS plugin: DTS installed with a two-node topology (SUT + TG)

## Installation

```bash
git clone --recurse-submodules <repo-url> autoforge
cd autoforge
make setup-runner
```

`make setup-runner` installs dev dependencies (pytest, ruff, pre-commit) and
checks for runner prerequisites (meson, ninja, compiler, pkg-config).

## Runner configuration

Run `uv run autoforge doctor --role runner` to validate your setup before starting the service.

Configuration uses **shared defaults + local overrides**. Shared `.toml`
files are tracked in git with sensible defaults. System-specific settings
go in `.local.toml` siblings (gitignored). Create only the overrides you
need:

```bash
# Override build directory and source path for your system
cat > projects/dpdk/runner.local.toml <<'EOF'
[paths]
build_dir = "/fast-ssd/dpdk-build"
EOF

# Override lcores and port config for your hardware
cat > projects/dpdk/tests/testpmd-memif.local.toml <<'EOF'
[testpmd]
lcores = "96-103"
vdev = [
    "net_memif0,role=server,id=0",
    "net_memif1,role=client,id=0,zero-copy=yes",
]
EOF
```

String values support `${VAR}` for environment variables and `${REPO_ROOT}`
for repo-relative paths. Use `${VAR:-default}` for optional variables.

The runner resolves framework config via: explicit path > `AUTOFORGE_CONFIG`
env var > `.autoforge.toml` pointer. Plugin configs are loaded automatically
from sibling `.toml` files next to each plugin `.py` file, with `.local.toml`
overrides merged on top.

**Framework config** (`projects/dpdk/runner.toml`):

| Section | Key | Description |
|---------|-----|-------------|
| `[runner]` | `phase` | Runner phase: `all` (default), `build`, `deploy`, or `test` |
| `[runner]` | `log_level` | Log level: `debug`, `info`, `warning`, `error` (default: info) |
| `[runner]` | `log_file` | Optional log file path (logs to stdout and file) |
| `[runner]` | `poll_interval` | Seconds between polling for requests (default: 30) |
| `[paths]` | `dpdk_src` | Absolute path to the DPDK source tree |
| `[paths]` | `build_dir` | Build artifact directory (created automatically) |
| `[paths]` | `dts_dir` | DTS installation path (DTS plugin only) |
| `[timeouts]` | `build_minutes` | Max build time before abort (default: 30) |
| `[timeouts]` | `test_minutes` | Max test time before abort (default: 10) |

**Build plugin config** (`projects/dpdk/builds/local.toml`):

| Section | Key | Description |
|---------|-----|-------------|
| `[build]` | `jobs` | Parallel build jobs (0 = all cores) |
| `[build]` | `cross_file` | Meson cross-file for cross-compiling (empty for native) |
| `[build]` | `extra_meson_args` | Additional meson setup arguments |

**Profiler plugin config** (`projects/dpdk/perfs/perf-record.toml`):

| Section | Key | Description |
|---------|-----|-------------|
| `[profiling]` | `enabled` | Capture `perf` profiles during testpmd measurement window (default: `false`) |
| `[profiling]` | `frequency` | Sampling frequency in Hz (default: `99`) |
| `[profiling]` | `sudo` | Run `perf` with sudo — must match `testpmd.sudo` (default: `true`) |

The deploy plugin (`deploys/local.py`) is a pass-through and needs no config file.

Override the config path with the `AUTOFORGE_CONFIG` environment variable.
The log level can also be set via the `LOG_LEVEL` environment variable.

## Plugin system

The runner uses a plugin architecture. Each request specifies which build,
deploy, and test plugins to use. Plugins live under
`projects/<project>/{builds,deploys,tests,perfs}/` as Python files.

The active project and sprint are set in `.autoforge.toml` at the repo root.
Campaign configuration (including plugin selection) is per-sprint at
`projects/<project>/sprints/<sprint>/campaign.toml`.

### Test plugins

The test plugin is selected in the sprint's `campaign.toml` via `[project].test`.

**testpmd-memif** — Runs testpmd with memif vdevs in io-fwd mode using
`--auto-start --tx-first`. Waits for warmup, runs for a measurement window,
then stops testpmd and computes bi-directional Mpps from accumulated forward
statistics.

Configure in `projects/dpdk/tests/testpmd-memif.toml`:

| Key | Description |
|-----|-------------|
| `[testpmd].lcores` | EAL lcore mask (e.g. `"4-7"`) |
| `[testpmd].pci` | PCI addresses of NIC ports |
| `[testpmd].vdev` | Virtual device strings (e.g. memif pair); each passed as `--vdev` to EAL |
| `[testpmd].no_pci` | Disable PCI bus scanning (default: `false`) |
| `[testpmd].extra_eal_args` | Additional EAL arguments (list of strings) |
| `[testpmd].nb_cores` | Forwarding cores (excluding main lcore) |
| `[testpmd].rxq` / `txq` | Queues per port |
| `[testpmd].rxd` / `txd` | Descriptors per queue |
| `[testpmd].burst` | RX/TX burst size (default: 32) |
| `[testpmd].forward_mode` | Forwarding mode: `"io"`, `"macswap"`, etc. (default: `"io"`) |
| `[testpmd].warmup_seconds` | Seconds before measurement starts |
| `[testpmd].measure_seconds` | Measurement window duration |
| `[testpmd].repeat_count` | Runs per measurement; median is reported (default: `1`). Use 3–5 for sub-1% gain detection. |
| `[testpmd].sudo` | Run testpmd with sudo (default: `true`) |

When using memif vdevs, the runner logs a warning at startup if a server-role
vdev has `zero-copy=yes` set — the memif PMD silently ignores zero-copy on
the server side; only the client role supports it.

See `projects/dpdk/runner.toml` for the full annotated list of options.

testpmd requires root for hugepages and device access. The runner uses `sudo`
by default. Configure passwordless sudo for the testpmd binary:

```bash
sudo visudo -f /etc/sudoers.d/dpdk-testpmd
```

Add a rule for your user (replace `dave` and the path as needed):

```
dave ALL=(root) NOPASSWD: /tmp/dpdk-build/app/dpdk-testpmd
```

Set `sudo = false` in `[testpmd]` if the runner service already runs as root.

**dts-mlx5** — Runs the DPDK Test Suite via `poetry run ./main.py` in the DTS
directory. Requires `[paths].dts_dir` in the project's `runner.toml` and DTS topology files.
Copy `config/nodes.yaml.example` → `config/nodes.yaml` and
`config/test_run.yaml.example` → `config/test_run.yaml` and fill in your
topology.

Set `test = "dts-mlx5"` in `[project]` in the sprint's `campaign.toml` and
configure the metric path to match the DTS JSON result structure.

### Profiling

When enabled, the runner captures `perf` profiles during the testpmd
measurement window. The profiling data (folded stacks and hardware counters)
is summarized and attached to the request results, giving the agent insight
into where CPU cycles are spent.

**Prerequisites:**

- Linux `perf` tool installed (`perf` or `linux-tools-$(uname -r)`)
- Kernel support for hardware performance counters
- If `profiling.sudo = true`: passwordless sudo for `perf` (same pattern as testpmd above)

**Enable in `projects/dpdk/perfs/perf-record.toml`** (or override in `perf-record.local.toml`):

```toml
[profiling]
enabled = true
frequency = 99    # sampling Hz (99 avoids timer aliasing)
sudo = true       # must match [testpmd].sudo when profiling testpmd
```

Also set `[profiling].enabled = true` in the sprint's `campaign.toml` so the
agent receives the profiling summary in its prompt context.

**What happens at runtime:**

1. `perf record` attaches to the testpmd process for the measurement window
2. Stacks are folded and parsed into a top-functions / hot-paths summary
3. Hardware counters (`perf stat`) capture IPC, cache misses, branch misses
4. The summary is included in the request result pushed back to the agent

The profiling library lives in `autoforge/perf/`: `profile.py` (capture),
`analyze.py` (stack analysis and diagnostics), `arch.py` (architecture
detection and PMU event profiles), `diff.py` (differential comparison between
runs), and `gate.py` (CI regression gate with pass/warn/fail thresholds).

## Running

```bash
uv run autoforge-runner
```

The runner supports four phase modes (configured via `[runner].phase`):

- `all` (default) — runs build → deploy → test sequentially
- `build` — builds only, transitions to `built`
- `deploy` — deploys built artifacts, transitions to `deployed`
- `test` — tests deployed artifacts, transitions to `completed`

**Full runner daemon loop (phase=all):**

1. `git pull --rebase` to fetch new requests
2. Scan `projects/<project>/sprints/<sprint>/requests/` for pending requests
3. Claim the first pending request (`pending` → `claimed`)
4. Build the project at the specified commit (`claimed` → `building` → `built`)
5. Deploy build artifacts (`built` → `deploying` → `deployed`)
6. Run test plugin (`deployed` → `running` → `completed` or `failed`)
7. Push results and sleep

The runner takes no CLI arguments. All configuration is via
`projects/<project>/runner.toml` and `.autoforge.toml` (for project/sprint
selection).

## Systemd deployment

Create a wrapper script at `/usr/local/bin/autoforge-runner`:

```bash
#!/bin/sh
cd /path/to/checkout && exec .venv/bin/autoforge-runner "$@"
```

The example below uses DPDK paths. Adjust `User`, `WorkingDirectory`, and
config path for your project.

Then create a systemd unit:

```bash
sudo tee /etc/systemd/system/autoforge-runner.service <<'EOF'
[Unit]
Description=Autoforge Runner
After=network.target

[Service]
Type=simple
User=dpdk
WorkingDirectory=/path/to/checkout
ExecStart=/usr/local/bin/autoforge-runner
Environment=AUTOFORGE_CONFIG=/etc/autoforge/dpdk-runner.toml
Restart=on-failure
RestartSec=30
ReadWritePaths=/var/lib/autoforge /tmp

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now autoforge-runner
```

Check logs with:

```bash
journalctl -u autoforge-runner -f
```

## Build pipeline

For each request, the runner:

1. Checks out the commit specified in the request
2. Runs `meson setup` with configured options (cross-file, extra args)
3. Runs `ninja` with the configured job count
4. Build artifacts are written to the configured `build_dir`

Build timeout is controlled by `timeouts.build_minutes` in the project's `runner.toml`.

## Troubleshooting

**Build failures**
Check the `build_log_snippet` field in the request JSON file. It contains the
last lines of build output. Common causes: missing dependencies, incompatible
compiler version, or meson configuration errors.

**Test failures**
For testpmd: check that PCI addresses and lcores are correct in `projects/dpdk/tests/testpmd-memif.toml`.
For DTS: check the DTS output directory for full test logs. The request JSON
`error` field contains the failure reason.

**Push conflicts**
The runner automatically retries push operations up to 3 times with
`git pull --rebase` between attempts. If all retries fail, the request is
marked as failed and logged.

**Stale requests**
On startup, the runner recovers any requests stuck in intermediate statuses
(`claimed`, `building`, `built`, `deploying`, `deployed`, `running`) by marking
them as `failed` with error "runner restarted". This handles the case where a
previous runner instance crashed mid-processing.
