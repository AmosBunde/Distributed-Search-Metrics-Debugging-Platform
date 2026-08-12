# Distributed Search Metrics & Debugging Platform
#
# Every target is documented with a `##` comment and listed by `make help`.
# Targets that are not implemented yet fail loudly with the issue that adds
# them, so nothing here silently does nothing.

SHELL := /bin/bash
.DEFAULT_GOAL := help

COMPOSE ?= docker compose

# Prefer the project virtualenv when it exists; `make install-dev` creates it.
# Many distributions ship an externally-managed Python that refuses `pip install`,
# so a venv is the supported way to get the tooling.
VENV ?= .venv
PYTHON ?= $(if $(wildcard $(VENV)/bin/python),$(VENV)/bin/python,python3)
PYTEST ?= $(PYTHON) -m pytest
SERVICES := telemetry-collector metrics-engine debug-service api-gateway query-simulator

# Load .env when present so targets can interpolate its values (ports, hosts).
# Deliberately NOT exported: docker compose reads .env by itself, and exporting
# would leak local configuration into every child process — including the test
# suite, which must run against known defaults.
ifneq (,$(wildcard .env))
include .env
endif

QPS ?= 100
SCENARIO ?= baseline
DURATION ?= 60
IMAGE_TAG ?= $(shell git rev-parse --short HEAD 2>/dev/null || echo dev)

define not_implemented
	@echo ""
	@echo "  ✗ '$@' is not implemented yet — it lands in issue #$(1)."
	@echo "    Track it: https://github.com/AmosBunde/Distributed-Search-Metrics-Debugging-Platform/issues/$(1)"
	@echo ""
	@exit 1
endef

.PHONY: help
help: ## List every target with a one-line description
	@echo ""
	@echo "  Distributed Search Metrics & Debugging Platform"
	@echo ""
	@grep -hE '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| sort \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}'
	@echo ""

# --- Local stack (issue #5) -------------------------------------------------

.PHONY: dev
dev: ## Start the full local stack and wait for it to be healthy
	$(call not_implemented,5)

.PHONY: down
down: ## Stop the local stack and remove containers
	$(call not_implemented,5)

.PHONY: logs
logs: ## Tail logs from every service (SERVICE=name for one)
	$(call not_implemented,5)

.PHONY: health
health: ## Check that every service in the stack is up
	$(call not_implemented,5)

.PHONY: check-kafka
check-kafka: ## Show Kafka topics and consumer lag
	$(call not_implemented,5)

.PHONY: check-metrics
check-metrics: ## Show recent row counts in ClickHouse
	$(call not_implemented,5)

# --- Traffic generation (issue #9) -----------------------------------------

.PHONY: simulate
simulate: ## Generate search traffic (QPS=500 SCENARIO=error_spike DURATION=60)
	$(call not_implemented,9)

# --- Tests ------------------------------------------------------------------

.PHONY: test-unit
test-unit: ## Run unit tests (no infrastructure required)
	$(PYTEST) tests/unit -q

.PHONY: test-integration
test-integration: ## Run integration tests (requires: make dev)
	$(call not_implemented,11)

.PHONY: test-e2e
test-e2e: ## Run end-to-end black-box tests (requires: make dev)
	$(call not_implemented,11)

.PHONY: coverage
coverage: ## Run unit tests with coverage and write htmlcov/
	$(PYTEST) tests/unit --cov --cov-report=term-missing --cov-report=html
	@echo "HTML report: htmlcov/index.html"

# --- Code quality -----------------------------------------------------------

.PHONY: lint
lint: ## Lint and format-check all Python code
	$(PYTHON) -m ruff check .
	$(PYTHON) -m ruff format --check .

.PHONY: format
format: ## Auto-format all Python code
	$(PYTHON) -m ruff format .
	$(PYTHON) -m ruff check --fix .

# --- Diagrams (issue #2) ----------------------------------------------------

.PHONY: diagrams
diagrams: ## Regenerate architecture diagrams from their specs (needs ARCHIFY_HOME)
	$(PYTHON) scripts/build_diagrams.py

# --- Images (issue #13) -----------------------------------------------------

.PHONY: build
build: ## Build container images for every service
	$(call not_implemented,5)

.PHONY: build-push
build-push: ## Build and push images to your cloud registry
	$(call not_implemented,13)

# --- Housekeeping -----------------------------------------------------------

.PHONY: install-dev
install-dev: ## Create .venv and install Python development dependencies
	@test -d $(VENV) || python3 -m venv $(VENV)
	@$(VENV)/bin/python -m pip install --quiet --upgrade pip
	@$(VENV)/bin/python -m pip install --quiet -r requirements-dev.txt
	@echo "Development environment ready: $(VENV)"
	@echo "Activate it with: source $(VENV)/bin/activate"

.PHONY: clean
clean: ## Remove caches, coverage output and build artifacts
	@find . -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
	@rm -rf .pytest_cache .ruff_cache htmlcov .coverage coverage.xml
	@echo "Cleaned."
