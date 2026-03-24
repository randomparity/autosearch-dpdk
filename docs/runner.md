# Runner Guide

The runner runs on a lab machine with DPDK hardware. It polls for test
requests, builds DPDK, runs performance tests (testpmd or DTS), and pushes
results back.

## Prerequisites

- Python 3.13+
- [uv](https://docs.astral.sh/uv/)
- DPDK build dependencies: meson, ninja, gcc (or clang), pkg-config
- Git access to the autosearch-dpdk repository (push permissions)
- For testpmd backend: NIC ports connected back-to-back
- For DTS backend: DTS installed with a two-node topology (SUT + TG)

## Installation

```bash
git clone --recurse-submodules <repo-url>
cd autosearch-dpdk
uv sync
```

## Runner configuration

Copy the example and fill in your environment paths:

```bash
cp config/runner.toml.example config/runner.toml
```

`config/runner.toml` is gitignored — never commit host-specific paths.

| Section | Key | Description |
|---------|-----|-------------|
| `[paths]` | `dpdk_src` | Absolute path to the DPDK source tree |
| `[paths]` | `build_dir` | Build artifact directory (created automatically) |
| `[paths]` | `dts_dir` | DTS installation path (DTS backend only) |
| `[timeouts]` | `build_minutes` | Max build time before abort (default: 30) |
| `[timeouts]` | `test_minutes` | Max test time before abort (default: 10) |
| `[build]` | `jobs` | Parallel build jobs (0 = all cores) |
| `[build]` | `cross_file` | Meson cross-file for cross-compiling (empty for native) |
| `[build]` | `extra_meson_args` | Additional meson setup arguments |

Override the config path with the `AUTOSEARCH_CONFIG` environment variable.

## Test backends

The test backend is selected in `config/campaign.toml` via `[test].backend`.

### testpmd (default)

Runs testpmd in io-fwd mode with `--tx-first` on back-to-back ports. Measures
bi-directional throughput in Mpps by parsing `show port stats all` output.

Configure in `config/runner.toml`:

| Key | Description |
|-----|-------------|
| `[testpmd].lcores` | EAL lcore mask (e.g. `"4-7"`) |
| `[testpmd].pci` | PCI addresses of NIC ports |
| `[testpmd].nb_cores` | Forwarding cores (excluding main lcore) |
| `[testpmd].rxq` / `txq` | Queues per port |
| `[testpmd].rxd` / `txd` | Descriptors per queue |
| `[testpmd].warmup_seconds` | Seconds before measurement starts |
| `[testpmd].measure_seconds` | Measurement window duration |
| `[testpmd].sudo` | Run testpmd with sudo (default: `true`) |

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

### DTS

Runs the DPDK Test Suite via `poetry run ./main.py` in the DTS directory.
Requires `[paths].dts_dir` in `runner.toml` and DTS topology files
(`nodes.yaml`, `test_run.yaml`) configured separately.

Set `backend = "dts"` in `config/campaign.toml` and configure the metric path
to match the DTS JSON result structure.

## Running

```bash
uv run python -m src.runner.service
```

The runner daemon loop:

1. `git pull --rebase` to fetch new requests
2. Scan `requests/` for pending requests
3. Claim the first pending request (status: `pending` -> `claimed`)
4. Build DPDK at the specified commit (`claimed` -> `building`)
5. Run test backend (`building` -> `running`)
6. Push results (`running` -> `completed` or `failed`)
7. Sleep and repeat

The runner takes no CLI arguments. All configuration is via `config/runner.toml`.

## Systemd deployment

A service unit file is provided at `runner/autosearch-runner.service`.

```bash
sudo cp runner/autosearch-runner.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now autosearch-runner
```

The service unit expects:
- `ExecStart=/usr/local/bin/autosearch-runner` — create a wrapper script or
  symlink to `uv run python -m src.runner.service` in your checkout
- `User=dpdk` — runs as a dedicated `dpdk` user
- `AUTOSEARCH_CONFIG=/etc/autosearch/runner.toml` — config path override
- `ReadWritePaths=/var/lib/autosearch /tmp` — writable paths for build artifacts

Check logs with:

```bash
journalctl -u autosearch-runner -f
```

## Build pipeline

For each request, the runner:

1. Checks out the DPDK commit specified in the request
2. Runs `meson setup` with configured options (cross-file, extra args)
3. Runs `ninja` with the configured job count
4. Build artifacts are written to the configured `build_dir`

Build timeout is controlled by `timeouts.build_minutes` in `runner.toml`.

## Troubleshooting

**Build failures**
Check the `build_log_snippet` field in the request JSON file. It contains the
last lines of build output. Common causes: missing dependencies, incompatible
compiler version, or meson configuration errors.

**Test failures**
For testpmd: check that PCI addresses and lcores are correct in `runner.toml`.
For DTS: check the DTS output directory for full test logs. The request JSON
`error` field contains the failure reason.

**Push conflicts**
The runner automatically retries push operations up to 3 times with
`git pull --rebase` between attempts. If all retries fail, the request is
marked as failed and logged.

**Stale requests**
On startup, the runner recovers any requests stuck in `claimed`, `building`, or
`running` status by marking them as `failed` with error "runner restarted".
This handles the case where a previous runner instance crashed mid-processing.
