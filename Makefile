.PHONY: setup setup-agent setup-runner check-common check-agent check-runner \
       venv lint format test clean

SHELL := /bin/bash
UV := uv
PYTHON := $(UV) run python
VENV := .venv

# ── Setup targets ──────────────────────────────────────────────────

setup: check-common venv  ## Dev setup (deps + hooks)
	$(UV) run pre-commit install
	@echo "Setup complete. Run 'uv run autoforge context' to verify."

setup-agent: check-common venv  ## Setup for agent workstation
	$(UV) sync --group dev --group agent
	$(UV) run pre-commit install
	@echo "Agent setup complete."

setup-runner: check-common check-runner venv  ## Setup for runner lab machine
	$(UV) run pre-commit install
	@echo "Runner setup complete."
	@test -f config/runner.toml || \
		echo "NOTE: Copy config/runner.toml.example to config/runner.toml and configure."

venv: pyproject.toml  ## Create venv and install deps
	$(UV) sync --group dev
	@echo "Virtual environment ready at $(VENV)/"

# ── Dependency checks ─────────────────────────────────────────────

check-common:
	@echo "Checking common dependencies..."
	@command -v uv >/dev/null 2>&1 || \
		{ echo "ERROR: uv not found. Install: curl -LsSf https://astral.sh/uv/install.sh | sh"; exit 1; }
	@command -v git >/dev/null 2>&1 || \
		{ echo "ERROR: git not found."; exit 1; }
	@git submodule status projects/dpdk/repo >/dev/null 2>&1 || \
		echo "WARN: dpdk submodule not initialized. Run: git submodule update --init"
	@echo "Common dependencies OK."

check-runner:
	@echo "Checking runner dependencies..."
	@command -v meson >/dev/null 2>&1 || \
		{ echo "ERROR: meson not found. Install: pip install meson (or dnf install meson)"; exit 1; }
	@command -v ninja >/dev/null 2>&1 || \
		{ echo "ERROR: ninja not found. Install: dnf install ninja-build"; exit 1; }
	@command -v gcc >/dev/null 2>&1 || command -v cc >/dev/null 2>&1 || \
		{ echo "ERROR: C compiler not found. Install gcc or clang."; exit 1; }
	@command -v pkg-config >/dev/null 2>&1 || command -v pkgconf >/dev/null 2>&1 || \
		{ echo "ERROR: pkg-config not found."; exit 1; }
	@command -v perf >/dev/null 2>&1 || \
		echo "WARN: perf not found (optional, needed for profiling)."
	@echo "Runner dependencies OK."

# ── Development targets ────────────────────────────────────────────

lint:  ## Run linter
	$(UV) run ruff check autoforge/ tests/

format:  ## Run formatter
	$(UV) run ruff format autoforge/ tests/

test:  ## Run tests
	$(UV) run pytest -q

clean:  ## Remove build artifacts and caches
	rm -rf $(VENV) .ruff_cache .pytest_cache autoforge/__pycache__
	find . -path ./projects/dpdk/repo -prune -o -name '__pycache__' -print | xargs rm -rf

# ── Help ───────────────────────────────────────────────────────────

help:  ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'
