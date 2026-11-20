.PHONY: help install dev test lint format build-mta deploy clean

PYTHON ?= python3
VENV_DIR ?= srv/.venv

help: ## Show available make targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-18s\033[0m %s\n", $$1, $$2}'

install: ## Create Python virtualenv and install srv + dev dependencies
	$(PYTHON) -m venv $(VENV_DIR)
	$(VENV_DIR)/bin/pip install --upgrade pip
	$(VENV_DIR)/bin/pip install -e "srv[dev]"

dev: ## Run the Python MCP server locally in mock auth mode on port 8080
	cd srv && AUTH_MODE=mock .venv/bin/uvicorn app.main:app --reload --host 0.0.0.0 --port 8080

test: ## Run pytest suite inside srv/
	cd srv && .venv/bin/pytest -v

lint: ## Run ruff linter and mypy type checks inside srv/
	cd srv && .venv/bin/ruff check app tests

build-mta: ## Build SAP BTP MTA archive (.mtar) using Cloud MTA Build Tool (mbt)
	mbt build -t mta_archives --mtar btp-cf-mcp.mtar

deploy: ## Deploy built MTA archive to SAP BTP Cloud Foundry
	cf deploy mta_archives/btp-cf-mcp.mtar

clean: ## Remove build artifacts, caches, and mta_archives
	rm -rf mta_archives srv/.pytest_cache srv/.ruff_cache srv/.mypy_cache
	find . -type d -name "__pycache__" -exec rm -rf {} +
