# Plugin SDK

Autoforge uses a file-based plugin system. Each plugin is a standalone Python
file that implements one of four protocols: Builder, Deployer, Tester, or
Profiler. The framework discovers plugins automatically — no registration or
entry points needed.

## Quick start

Scaffold a new project and create your first plugin:

```bash
uv run autoforge project init linux-kernel
```

This creates:

```
projects/linux-kernel/
  builds/
  deploys/
  tests/
  perfs/
  sprints/
```

Create a builder at `projects/linux-kernel/builds/local.py`:

```python
from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Any

from autoforge.campaign import ProjectConfig
from autoforge.plugins.protocols import BuildResult


class KernelBuilder:
    name = "local"

    def configure(
        self, project_config: ProjectConfig, runner_config: dict[str, Any],
    ) -> None:
        self._cfg = runner_config.get("build", {})

    def build(
        self, source_path: Path, commit: str, build_dir: Path, timeout: int,
    ) -> BuildResult:
        start = time.monotonic()
        try:
            subprocess.run(
                ["git", "checkout", commit],
                cwd=source_path, check=True, capture_output=True, timeout=30,
            )
            subprocess.run(
                ["make", "olddefconfig"],
                cwd=source_path, check=True, capture_output=True, timeout=60,
            )
            jobs = self._cfg.get("jobs", 0) or "$(nproc)"
            result = subprocess.run(
                ["make", f"-j{jobs}"],
                cwd=source_path, capture_output=True, text=True, timeout=timeout,
            )
            if result.returncode != 0:
                return BuildResult(
                    success=False,
                    log=result.stderr[-2000:],
                    duration_seconds=time.monotonic() - start,
                )
            vmlinuz = source_path / "arch" / "x86" / "boot" / "bzImage"
            return BuildResult(
                success=True,
                log=result.stdout[-2000:],
                duration_seconds=time.monotonic() - start,
                artifacts={"vmlinuz": str(vmlinuz), "source": str(source_path)},
            )
        except subprocess.TimeoutExpired:
            return BuildResult(
                success=False, log="TIMEOUT", duration_seconds=time.monotonic() - start,
            )
```

Create a config example at `projects/linux-kernel/builds/local.toml.example`:

```toml
[build]
jobs = 0
```

Initialize a sprint and set the plugin names in the campaign config:

```bash
uv run autoforge sprint init 2026-04-01-baseline
```

Edit `projects/linux-kernel/sprints/2026-04-01-baseline/campaign.toml`:

```toml
[project]
name = "linux-kernel"
build = "local"
deploy = "local"
test = "your-benchmark"
```

## Plugin discovery

Plugins live under `projects/<project>/{builds,deploys,tests,perfs}/`. Each
`.py` file must contain exactly one class that conforms to the corresponding
protocol. The class name is arbitrary — the loader finds it by protocol
conformance, not by name.

**Requirements for every plugin class:**

1. A `name` class attribute (string, used for logging)
2. A `configure(self, project_config, runner_config) -> None` method
3. The protocol-specific method (`build`, `deploy`, `test`, or `profile`)
4. A no-argument constructor (the loader calls `cls()` with no args)

## Builder

Compiles project source into build artifacts.

```python
from autoforge.campaign import ProjectConfig
from autoforge.plugins.protocols import BuildResult

class MyBuilder:
    name = "local"

    def configure(
        self, project_config: ProjectConfig, runner_config: dict[str, Any],
    ) -> None:
        """Called once at startup. Store config for later."""

    def build(
        self, source_path: Path, commit: str, build_dir: Path, timeout: int,
    ) -> BuildResult:
        """Check out commit, build, return result."""
```

**Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `source_path` | `Path` | Project source root |
| `commit` | `str` | Git commit SHA to check out |
| `build_dir` | `Path` | Where to write build artifacts |
| `timeout` | `int` | Max build time in seconds |

**BuildResult fields:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `success` | `bool` | yes | Whether the build succeeded |
| `log` | `str` | yes | Build output (truncate to ~2000 chars) |
| `duration_seconds` | `float` | yes | Actual build time |
| `artifacts` | `dict` | no | Paths/data passed to the deployer (default: `{}`) |

## Deployer

Takes build artifacts and makes them available for testing. For bare-metal
builds on the same machine, this can be a pass-through. For remote targets,
this is where you copy files, push containers, or restart services.

```python
from autoforge.campaign import ProjectConfig
from autoforge.plugins.protocols import BuildResult, DeployResult

class MyDeployer:
    name = "local"

    def configure(
        self, project_config: ProjectConfig, runner_config: dict[str, Any],
    ) -> None:
        pass

    def deploy(self, build_result: BuildResult) -> DeployResult:
        """Deploy build artifacts. Return target info for the tester."""
```

**DeployResult fields:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `success` | `bool` | yes | Whether deployment succeeded |
| `error` | `str \| None` | no | Error message (default: `None`) |
| `target_info` | `dict` | no | Info passed to tester (default: `{}`) |

### Example: Podman container deployer

```python
from __future__ import annotations

import subprocess
from typing import Any

from autoforge.plugins.protocols import BuildResult, DeployResult


class PodmanDeployer:
    name = "podman"

    def configure(
        self, project_config: ProjectConfig, runner_config: dict[str, Any],
    ) -> None:
        self._cfg = runner_config.get("podman", {})

    def deploy(self, build_result: BuildResult) -> DeployResult:
        image = self._cfg.get("image", "localhost/my-app:test")
        dockerfile = build_result.artifacts.get("dockerfile", "Dockerfile")
        context = build_result.artifacts.get("build_dir", ".")

        try:
            subprocess.run(
                ["podman", "build", "-t", image, "-f", dockerfile, context],
                check=True, capture_output=True, text=True, timeout=300,
            )
            result = subprocess.run(
                ["podman", "run", "-d", "--rm", "-p", "8080:8080", image],
                check=True, capture_output=True, text=True, timeout=30,
            )
            container_id = result.stdout.strip()
            return DeployResult(
                success=True,
                target_info={
                    "container_id": container_id,
                    "host": "localhost",
                    "port": 8080,
                },
            )
        except subprocess.CalledProcessError as exc:
            return DeployResult(success=False, error=exc.stderr[:500])
        except subprocess.TimeoutExpired:
            return DeployResult(success=False, error="deploy timed out")
```

Config at `projects/my-app/deploys/podman.toml.example`:

```toml
[podman]
image = "localhost/my-app:test"
```

## Tester

Runs the actual benchmark or test and returns the metric the agent optimizes.

```python
from autoforge.campaign import ProjectConfig
from autoforge.plugins.protocols import DeployResult, TestResult

class MyTester:
    name = "wrk-bench"

    def configure(
        self, project_config: ProjectConfig, runner_config: dict[str, Any],
    ) -> None:
        """Store test parameters."""

    def test(self, deploy_result: DeployResult, timeout: int) -> TestResult:
        """Run benchmark, return metric."""
```

**Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `deploy_result` | `DeployResult` | Contains `target_info` from deployer |
| `timeout` | `int` | Max test time in seconds |

**TestResult fields:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `success` | `bool` | yes | Whether the test completed |
| `metric_value` | `float \| None` | yes | The number the agent optimizes |
| `results_json` | `dict \| None` | no | Structured results (stored in request) |
| `results_summary` | `str \| None` | no | Human-readable summary |
| `error` | `str \| None` | no | Error message if failed |
| `duration_seconds` | `float` | yes | Actual test time |

`metric_value` is the single number the agent tracks across iterations. The
campaign config specifies `[metric].direction` (`maximize` or `minimize`) and
`[metric].threshold` (minimum improvement to keep a change).

### Example: HTTP benchmark tester

```python
from __future__ import annotations

import json
import re
import subprocess
import time
from typing import Any

from autoforge.plugins.protocols import DeployResult, TestResult


class WrkBenchTester:
    name = "wrk-bench"

    def configure(
        self, project_config: ProjectConfig, runner_config: dict[str, Any],
    ) -> None:
        self._cfg = runner_config.get("wrk", {})

    def test(self, deploy_result: DeployResult, timeout: int) -> TestResult:
        host = deploy_result.target_info.get("host", "localhost")
        port = deploy_result.target_info.get("port", 8080)
        duration = self._cfg.get("duration", "30s")
        threads = self._cfg.get("threads", 4)
        connections = self._cfg.get("connections", 100)
        start = time.monotonic()

        try:
            result = subprocess.run(
                [
                    "wrk", f"-t{threads}", f"-c{connections}",
                    f"-d{duration}", f"http://{host}:{port}/",
                ],
                capture_output=True, text=True, timeout=timeout,
            )
            elapsed = time.monotonic() - start

            if result.returncode != 0:
                return TestResult(
                    success=False, metric_value=None, results_json=None,
                    results_summary=None, error=result.stderr[:500],
                    duration_seconds=elapsed,
                )

            rps = _parse_requests_per_sec(result.stdout)
            return TestResult(
                success=True,
                metric_value=rps,
                results_json={"requests_per_sec": rps},
                results_summary=f"{rps:.0f} req/s",
                error=None,
                duration_seconds=elapsed,
            )
        except subprocess.TimeoutExpired:
            return TestResult(
                success=False, metric_value=None, results_json=None,
                results_summary=None, error="wrk timed out",
                duration_seconds=time.monotonic() - start,
            )


def _parse_requests_per_sec(output: str) -> float | None:
    match = re.search(r"Requests/sec:\s+([\d.]+)", output)
    return float(match.group(1)) if match else None
```

Config at `projects/my-app/tests/wrk-bench.toml.example`:

```toml
[wrk]
duration = "30s"
threads = 4
connections = 100
```

## Profiler

Captures performance profiles during the test measurement window. This is
optional — not all projects need profiling.

```python
from autoforge.campaign import ProjectConfig
from autoforge.plugins.protocols import ProfileResult

class MyProfiler:
    name = "perf-record"

    def configure(
        self, project_config: ProjectConfig, runner_config: dict[str, Any],
    ) -> None:
        self._cfg = runner_config.get("profiling", {})

    def profile(
        self, pid: int, duration: int, config: dict[str, Any],
    ) -> ProfileResult:
        """Attach to a running process and capture a profile."""
```

**Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `pid` | `int` | Process ID to profile |
| `duration` | `int` | Profile window in seconds |
| `config` | `dict` | Per-call config (merged with stored config) |

**ProfileResult fields:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `success` | `bool` | yes | Whether profiling succeeded |
| `summary` | `dict` | no | Profile data (default: `{}`) |
| `error` | `str \| None` | no | Error message (default: `None`) |
| `duration_seconds` | `float` | no | Actual profiling time (default: `0.0`) |

The existing `perf-record` profiler in `projects/dpdk/perfs/` uses
`autoforge.perf.profile.profile_pid()` for Linux perf integration. For other
platforms or tools (e.g., DTrace, Instruments), implement the same interface
with your profiling tool.

### Profiler troubleshooting

If `profiling.enabled = true` in your campaign config but no profiling data
appears in results, check these common issues on the runner machine:

1. **perf not installed.** The `perf` binary must be available. On RHEL/CentOS:
   `yum install perf`. On Ubuntu: `apt install linux-tools-$(uname -r)`.

2. **Insufficient permissions.** Check the kernel paranoid level:

   ```bash
   cat /proc/sys/kernel/perf_event_paranoid
   ```

   Must be `<= 1` for non-root profiling. Set it with:

   ```bash
   sudo sysctl kernel.perf_event_paranoid=1
   ```

3. **Verify perf works.** Run a quick test:

   ```bash
   perf stat -a sleep 1
   ```

   If this fails, profiling will silently return empty results.

4. **Profile plugin not set.** Ensure `[project] profiler = "perf-record"` is
   in your campaign.toml. An empty profiler field skips profiling even when
   `profiling.enabled = true`.

## Configuration

### Framework config: `projects/<project>/runner.toml`

Shared settings used by the runner framework:

```toml
[runner]
phase = "all"       # all, build, deploy, or test
log_level = "info"
poll_interval = 30

[paths]
dpdk_src = "/home/user/src/project"    # key names are project-specific
build_dir = "/tmp/project-build"

[timeouts]
build_minutes = 30
test_minutes = 10
```

### Plugin config: sibling `.toml`

Each plugin can have a sibling `.toml` file with the same stem as its `.py`
file. The loader merges this over the framework config before calling
`configure()`:

```
projects/my-app/tests/wrk-bench.py        # plugin code
projects/my-app/tests/wrk-bench.toml      # plugin config (gitignored)
projects/my-app/tests/wrk-bench.toml.example  # template (checked in)
```

**Merge order:** `{**runner_config, **plugin_config}` — plugin sections
override framework sections. Framework `[paths]` and `[timeouts]` are
available to all plugins unless the plugin overrides them.

If no sibling `.toml` exists, the plugin receives only the framework config.

### Campaign config

Each sprint has a `campaign.toml` that names the plugins to use:

```toml
[campaign]
name = "my-app optimization"
max_iterations = 50

[project]
name = "my-app"
build = "local"
deploy = "podman"
test = "wrk-bench"
# profiler = "perf-record"  # optional

[metric]
name = "requests_per_sec"
path = "requests_per_sec"   # dot-separated path into results_json
direction = "maximize"
threshold = 1.0             # minimum % improvement to keep a change
```

## Testing plugins

Use the `importlib` pattern to load your plugin in tests:

```python
import importlib.util
import sys
from pathlib import Path

PLUGIN_PATH = Path(__file__).parent.parent / "projects" / "my-app" / "builds" / "local.py"
MODULE_NAME = "test_my_builder"

spec = importlib.util.spec_from_file_location(MODULE_NAME, PLUGIN_PATH)
mod = importlib.util.module_from_spec(spec)
sys.modules[MODULE_NAME] = mod
spec.loader.exec_module(mod)

MyBuilder = mod.KernelBuilder
```

Mock `subprocess.run` for external commands:

```python
from unittest.mock import patch

class TestBuild:
    @patch("subprocess.run")
    def test_success(self, mock_run, tmp_path):
        mock_run.return_value = subprocess.CompletedProcess([], 0, stdout="ok", stderr="")
        builder = MyBuilder()
        builder.configure({}, {"build": {"jobs": 4}})
        result = builder.build(tmp_path, "abc123", tmp_path / "build", 300)
        assert result.success is True
```

## Checklist for new plugins

- [ ] Plugin file at `projects/<project>/<category>/<name>.py`
- [ ] `from __future__ import annotations` at top of file
- [ ] Class with `name` attribute matching the filename stem
- [ ] `configure()` method stores relevant config
- [ ] Protocol method returns the correct Result dataclass
- [ ] All `subprocess` calls include `timeout=`
- [ ] Sibling `.toml.example` checked in (if config needed)
- [ ] `.toml` added to `.gitignore` pattern
- [ ] Campaign `[project]` section updated with plugin name
- [ ] Tests written using `importlib` loader pattern
