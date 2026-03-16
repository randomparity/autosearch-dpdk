# Runner Guide

The runner runs on a lab machine with DTS infrastructure (SUT + traffic
generator). It polls for test requests, builds DPDK, runs DTS tests, and
pushes results back.

## Prerequisites

- Python 3.13+
- [uv](https://docs.astral.sh/uv/)
- DPDK build dependencies: meson, ninja, gcc (or clang), pkg-config
- DTS installed and configured with a two-node topology (SUT + TG)
- Git access to the autosearch-dpdk repository (push permissions)

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
| `[paths]` | `dts_dir` | Absolute path to the DTS installation |
| `[paths]` | `build_dir` | Build artifact directory (created automatically) |
| `[paths]` | `python` | Python interpreter for DTS (e.g. DTS venv path) |
| `[timeouts]` | `build_minutes` | Max build time before abort (default: 30) |
| `[timeouts]` | `test_minutes` | Max DTS test time before abort (default: 60) |
| `[git]` | `remote` | Git remote name (default: `"origin"`) |
| `[git]` | `base_branch` | Branch patches are applied on (default: `"main"`) |
| `[git]` | `patch_branch` | Iteration branch template (`{iteration}` replaced) |
| `[build]` | `jobs` | Parallel build jobs (0 = all cores) |
| `[build]` | `cross_file` | Meson cross-file for cross-compiling (empty for native) |
| `[build]` | `extra_meson_args` | Additional meson setup arguments |

Override the config path with the `AUTOSEARCH_CONFIG` environment variable.

## DTS configuration

Copy the example files and fill in lab-specific values:

```bash
cp config/nodes.yaml.example config/nodes.yaml
cp config/test_run.yaml.example config/test_run.yaml
```

Both files are gitignored.

**`config/nodes.yaml`** — defines the two-node topology:
- `sut` (system under test): hostname, PCI addresses, hugepage config, lcores
- `tg` (traffic generator): same fields for the traffic generator node

**`config/test_run.yaml`** — defines the test run:
- Build target (architecture, compiler)
- Test suites and cases to run
- Performance settings: trial duration, trial count, packet sizes, forwarding
  mode, queue and descriptor counts

See the example files for all available fields and their descriptions.

## Running

```bash
uv run python -m src.runner.service
```

The runner daemon loop:

1. `git pull --rebase` to fetch new requests
2. Scan `requests/` for pending requests
3. Claim the first pending request (status: `pending` -> `claimed`)
4. Build DPDK at the specified commit (`claimed` -> `building`)
5. Run DTS tests (`building` -> `running`)
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

**DTS test failures**
Check the DTS output directory for full test logs. The request JSON `error`
field contains the failure reason.

**Push conflicts**
The runner automatically retries push operations up to 3 times with
`git pull --rebase` between attempts. If all retries fail, the request is
marked as failed and logged.

**Stale requests**
On startup, the runner recovers any requests stuck in `claimed`, `building`, or
`running` status by marking them as `failed` with error "runner restarted".
This handles the case where a previous runner instance crashed mid-processing.
