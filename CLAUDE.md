# Invoice Automation — Missan

## What this project is
A fully local system that reads supplier invoices, extracts data with Qwen3-VL-4B,
validates and codes it, lets accountants review it in the Review Workbench, and posts
approved documents to the client's accounting system (Xero first). The app is fully usable
with no accounting system connected (CSV master data in, export file out).

**Hard rule: no client data ever leaves Missan's local servers. No third-party LLM APIs. Ever.**

Full design: `docs/architecture.md`. Phase plan: `docs/phases.md`. Decisions: `docs/decisions.md`.

## Current focus
**MVP (Phases 1–5).** Functional first, polish later.
Do NOT add unless explicitly asked: monitoring stack, MinIO, fraud alerts, four-eyes,
quick-confirm grid, fine-tuning, Kubernetes, extra duplicate checks beyond hash + business key.

## Stack
- Backend: Python 3.12, FastAPI, Pydantic v2, pydantic-settings, SQLAlchemy 2.0, Alembic
- Jobs: Celery + Redis
- DB: PostgreSQL 16 with pg_trgm
- Reading: PyMuPDF (digital PDFs), OpenCV + PaddleOCR 3.0.2 pinned (scans/photos; decision 22)
- Model: Qwen3-VL-4B via Ollama (dev, native on Mac) / vLLM (prod), OpenAI-compatible API
- Accounting systems: adapters behind one port; Xero via xero-python SDK (Phase 5); none required
- Frontend (`workbench/`): React + TypeScript + Vite, react-pdf, React Hook Form, TanStack Query
- Tooling: uv, ruff, mypy, pytest, pre-commit, Docker Compose

## Repo layout
```
backend/app/
  api/         FastAPI routers — thin, no business logic
  domain/      Invoice schema, enums, state machine (the core — change carefully)
  pipeline/    ingest, read, extract, verify, coding
  adapters/
    llm/       ExtractionClient (Ollama now, vLLM later)
    accounting/  AccountingSystem port + one folder per provider (manual/, xero/, later zoho/ ...)
    storage/   Storage interface (local filesystem now, MinIO later)
  workers/     Celery tasks — thin, call pipeline/services
  models/      SQLAlchemy tables
  core/        config, db session, logging
backend/migrations/   Alembic
backend/tests/
workbench/            React app
infra/                Docker/DB init and (later) deployment config
```

## Architecture rules (non-negotiable)
1. **Money is `Decimal`, never `float`.** Totals tolerance ±0.01.
2. **One canonical `Invoice` schema** in `app/domain/invoice.py`. Everything uses it.
3. **Accounting-system-specific types/codes only inside `app/adapters/accounting/<provider>/`.**
   The rest of the app uses canonical types and never knows which system a client uses.
4. **The model is only called through `ExtractionClient`.** Model name and URL come from settings, never hardcoded.
5. **Files only through the `Storage` interface.**
6. **Status changes only through `domain/state_machine.transition()`**, which also writes `audit_log`.
   Only `APPROVED` documents can be posted — enforced in code and by a DB constraint.
7. **Validation is deterministic Python.** Never use the LLM to check the LLM.
8. **Flag, never silently fix** extracted values.
9. **Null over invented values.** Schema fields are nullable unless every real invoice has them.
10. **Every table has `client_id`.**
11. **Approve = status update + outbox row in ONE transaction.** Only the Celery posting worker calls the accounting adapter.
12. **Posting is idempotent:** lookup in the target system before create, idempotency key, external ID saved.
13. **The Xero adapter posts as `SUBMITTED` only in the MVP.**
14. **`account_code` options are built at runtime** from the client's active ledger accounts
    (imported or synced master data) — never hardcoded enums.

## Coding conventions
- Type hints everywhere; mypy strict on `app/`.
- Routers and Celery tasks stay thin; logic lives in `pipeline/` and services.
- Celery tasks must be safe to re-run.
- Config via pydantic-settings; secrets only in `.env` (never committed; keep `.env.example` updated).
- No invoice amounts, TRNs or vendor bank details in logs.
- Every validation rule has unit tests.
- No new dependency without stating why.

## Commands
```
make up        # start Postgres + Redis
make down
make migrate   # alembic upgrade head
make api       # run FastAPI
make worker    # run Celery workers
make web       # run Workbench
make lint      # ruff + mypy
make test      # pytest
```
Ollama runs natively (not in Docker): `ollama serve`, model `qwen3-vl:4b-instruct`
(never plain `qwen3-vl:4b`: that tag is the Thinking variant; decision 2).

## How to work in this repo (Claude Code)
- Read this file and the relevant part of `docs/` before any non-trivial task.
- For any task touching more than 2 files: **propose a plan first, wait for approval.**
- Work in small steps; one logical change per commit (conventional commits).
- Before saying a task is done: run `make lint && make test` and fix all failures.
- Never change the `Invoice` schema, state machine or DB constraints without flagging it clearly.
- If something in the docs is unclear or conflicts, ask instead of guessing.

## Glossary
- ACCPAY / ACCREC: Xero supplier bill / sales invoice
- ACCPAYCREDIT: Xero supplier credit note
- TRN: UAE Tax Registration Number (15 digits)
- Outbox: Postgres table of approved documents waiting to post
- Ready / Check / Exception: verification result shown in the Workbench
