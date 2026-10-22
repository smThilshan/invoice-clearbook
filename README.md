# Invoice Automation — Missan

Local system that reads supplier invoices, extracts data with Qwen3-VL-4B, validates and codes it,
lets accountants review it in the Workbench, and posts approved documents to the accounting system (Xero first) — or exports them when none is connected.
No client data leaves the local servers.

- What we build: `docs/architecture.md`
- Build plan: `docs/phases.md`
- Settled decisions: `docs/decisions.md`
- Rules for Claude Code: `CLAUDE.md`

## Requirements (dev)
- macOS, Docker Desktop, uv, Node 20+, Ollama
- `ollama pull qwen3-vl:4b-instruct` (never plain `qwen3-vl:4b`: that tag is the Thinking variant; decision 2)
- Optional: Xero developer app (Client ID/Secret) + Demo Company, only to test the Xero adapter (Phase 5)

## Run
```bash
cp .env.example .env      # fill in values
make install              # backend deps + git hooks
ollama serve              # separate terminal, native (not Docker)
make up                   # Postgres + Redis
make migrate
make api                  # FastAPI → curl localhost:8000/health/ready
make worker               # Celery
make web                  # Workbench
```

## Set up a client (once each)
A client is any business whose books Missan keeps. Create it on the Run screen (**+ New client**:
just a name), or from the terminal with its own folder (decisions 25, 35):
```bash
make add-client ID=cafe-al-noor NAME="Cafe Al Noor" INBOX="/Volumes/Shared/Clients/Cafe Al Noor"
make import-master-data DIR=path/to/its/csvs CLIENT=cafe-al-noor   # chart of accounts, VAT, suppliers
make list-clients
```
For development, `make seed` loads fictional F&B master data for the default client.

## Use
1. Accountants keep files in folders as they do today (e.g. `October 2026/21/`), on this Mac or a shared drive
2. Workbench → Run → pick or create the client → pick a place (the client's folder, This Mac, Shared drives) → open a folder (sub-folders included) → confirm the date → Run
3. Review, approve → exported, or posted to Xero as Awaiting Approval when connected

## Develop
```bash
make check                # lint + unit tests; run before every commit
make test-all             # also integration tests (needs make up)
make help                 # all targets
```
