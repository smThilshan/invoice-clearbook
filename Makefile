.DEFAULT_GOAL := help
SHELL := /bin/bash

BACKEND := backend
UV := uv run --directory $(BACKEND)
COMPOSE := docker compose

.PHONY: help install up down logs migrate migration api worker web web-install web-types web-lint web-test lint fmt test test-all check smoke-llm smoke-read seed import-master-data resume retry-document add-client list-clients

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

worker: ## Run a Celery worker on both queues (needs make up). Solo pool: prefork fails on macOS
	$(UV) celery -A app.workers.celery_app worker -Q pipeline,posting --pool=solo --loglevel=INFO

WEB := npm --prefix workbench

web: ## Run the Workbench (http://127.0.0.1:5173; needs make api)
	$(WEB) run dev

web-install: ## Install Workbench dependencies
	$(WEB) ci

web-types: ## Regenerate the Workbench's API types from the backend
	@$(UV) python scripts/export_openapi.py $(CURDIR)/workbench/src/api/openapi.json
	@cd workbench && npx openapi-typescript src/api/openapi.json -o src/api/schema.d.ts

web-lint: ## Workbench: oxlint + TypeScript
	$(WEB) run lint
	$(WEB) run typecheck

web-test: ## Workbench: Vitest
	$(WEB) run test

resume: ## Re-queue documents stranded between pipeline steps (needs make worker)
	@$(UV) python scripts/pipeline_admin.py resume

retry-document: ## Send a FAILED document back to the start: make retry-document ID=<uuid>
	@test -n "$(ID)" || { echo 'usage: make retry-document ID=<document uuid>'; exit 1; }
	@$(UV) python scripts/pipeline_admin.py retry "$(ID)"

add-client: ## Register a restaurant: make add-client ID=cafe-al-noor NAME="Cafe Al Noor" INBOX=/path
	@test -n "$(ID)" -a -n "$(NAME)" -a -n "$(INBOX)" || { echo 'usage: make add-client ID=<id> NAME="<name>" INBOX=<folder>'; exit 1; }
	@$(UV) python scripts/manage_clients.py add "$(ID)" "$(NAME)" "$(INBOX)" --base "$(CURDIR)"

list-clients: ## List registered restaurants
	@$(UV) python scripts/manage_clients.py list

seed: ## Load the fictional F&B sample master data for the default client
	@$(UV) python scripts/import_master_data.py $(CURDIR)/infra/samples/master_data

import-master-data: ## Import master data CSVs: make import-master-data DIR=path [CLIENT=id]
	@test -n "$(DIR)" || { echo 'usage: make import-master-data DIR=path [CLIENT=id]'; exit 1; }
	@$(UV) python scripts/import_master_data.py "$(abspath $(DIR))" $(if $(CLIENT),--client $(CLIENT))

smoke-read: ## Read step (text layer / OCR) on data/samples/, metrics only
	@$(UV) python scripts/smoke_read.py

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

check: lint test web-lint web-test ## backend + Workbench lint and tests (run before every commit)
