.PHONY: setup setup-agent setup-runner check-common check-agent check-runner \
       venv lint format test clean

SHELL := /bin/bash
UV := uv
PYTHON := $(UV) run python
VENV := .venv

# ── Setup targets ──────────────────────────────────────────────────

setup: check-common venv  ## Full setup (common deps + venv + hooks)
	$(UV) run pre-commit install
	@echo "Setup complete. Run 'uv run autoforge context' to verify."

setup-agent: check-common check-agent venv  ## Setup for agent workstation
	@echo "Agent setup complete."

setup-runner: check-common check-runner venv  ## Setup for runner lab machine
	@echo "Runner setup complete."

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
	@command -v ruff >/dev/null 2>&1 || uv tool install ruff >/dev/null 2>&1 || \
		echo "WARN: ruff not found globally (will use 'uv run ruff' from venv)"
	@git submodule status dpdk >/dev/null 2>&1 || \
		{ echo "WARN: dpdk submodule not initialized. Run: git submodule update --init"; }
	@echo "Common dependencies OK."

check-agent:
	@echo "Checking agent dependencies..."
	@$(UV) run python -c "import anthropic" 2>/dev/null || \
		echo "WARN: anthropic not importable yet (will be installed by 'make venv')"
	@test -n "$${ANTHROPIC_API_KEY:-}" || test -f .env || \
		echo "WARN: ANTHROPIC_API_KEY not set and no .env file found."
	@test -f config/campaign.toml || \
		{ echo "ERROR: config/campaign.toml not found."; exit 1; }
	@echo "Agent dependencies OK."

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
	@test -f config/runner.toml || \
		{ echo "ERROR: config/runner.toml not found. Copy from runner.toml.example and configure."; exit 1; }
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
	find . -path ./dpdk -prune -o -name '__pycache__' -print | xargs rm -rf

# ── Help ───────────────────────────────────────────────────────────

help:  ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'
