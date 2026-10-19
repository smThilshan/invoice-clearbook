.DEFAULT_GOAL := help
SHELL := /bin/bash

BACKEND := backend
UV := uv run --directory $(BACKEND)
COMPOSE := docker compose

.PHONY: help install up down logs migrate api worker web lint fmt test check

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

migrate: ## Apply DB migrations (task 1.4)
	@echo "not yet implemented: alembic arrives in task 1.4"; exit 1

api: ## Run FastAPI (task 1.2)
	@echo "not yet implemented: FastAPI app arrives in task 1.2"; exit 1

worker: ## Run Celery workers (task 2.2)
	@echo "not yet implemented: Celery arrives in task 2.2"; exit 1

web: ## Run the Workbench (task 4.1)
	@echo "not yet implemented: Workbench arrives in task 4.1"; exit 1

lint: ## ruff + mypy
	$(UV) ruff check .
	$(UV) ruff format --check .
	$(UV) mypy

fmt: ## Auto-fix lint and format
	$(UV) ruff check --fix .
	$(UV) ruff format .

test: ## pytest
	$(UV) pytest

check: lint test ## lint + test (run before every commit)
