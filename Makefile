.DEFAULT_GOAL := help
SHELL := /bin/bash

BACKEND := backend
UV := uv run --directory $(BACKEND)
COMPOSE := docker compose

.PHONY: help install up down logs migrate migration api worker web lint fmt test test-all check smoke-llm seed import-master-data

help: ## List targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "} {printf "  %-10s %s\n", $$1, $$2}'

install: ## Install backend deps and git hooks
	uv sync --directory $(BACKEND)
	$(UV) pre-commit install

up: ## Start Postgres + Redis
	@test -f .env || { echo ".env missing: cp .env.example .env"; exit 1; }
	$(COMPOSE) up -d --wait

down: ## Stop Postgres + Redis (data kept)
	$(COMPOSE) down

logs: ## Tail Postgres + Redis logs
	$(COMPOSE) logs -f

migrate: ## Apply DB migrations
	$(UV) alembic upgrade head

migration: ## New migration from model changes: make migration m="add foo"
	@test -n "$(m)" || { echo 'usage: make migration m="what changed"'; exit 1; }
	$(UV) alembic revision --autogenerate -m "$(m)"

api: ## Run FastAPI on 127.0.0.1:8000 (reload)
	$(UV) uvicorn app.main:create_app --factory --reload --host 127.0.0.1 --port 8000

worker: ## Run Celery workers (task 2.2)
	@echo "not yet implemented: Celery arrives in task 2.2"; exit 1

web: ## Run the Workbench (task 4.1)
	@echo "not yet implemented: Workbench arrives in task 4.1"; exit 1

seed: ## Load the fictional F&B sample master data for the default client
	@$(UV) python scripts/import_master_data.py $(CURDIR)/infra/samples/master_data

import-master-data: ## Import master data CSVs: make import-master-data DIR=path [CLIENT=id]
	@test -n "$(DIR)" || { echo 'usage: make import-master-data DIR=path [CLIENT=id]'; exit 1; }
	@$(UV) python scripts/import_master_data.py "$(abspath $(DIR))" $(if $(CLIENT),--client $(CLIENT))

smoke-llm: ## Model smoke test on data/samples/, metrics only (ARGS=--show-values)
	@$(UV) python scripts/smoke_extraction.py $(ARGS) $(FILES)

lint: ## ruff + mypy
	$(UV) ruff check .
	$(UV) ruff format --check .
	$(UV) mypy

fmt: ## Auto-fix lint and format
	$(UV) ruff check --fix .
	$(UV) ruff format .

test: ## pytest (unit only)
	$(UV) pytest

test-all: ## pytest incl. integration (needs make up)
	$(UV) pytest -m ""

check: lint test ## lint + test (run before every commit)
