# Invoice Automation — Missan

Local system that reads supplier invoices, extracts data with Qwen3-VL-4B, validates and codes it,
lets accountants review it in the Workbench, and posts approved documents to Xero.
No client data leaves the local servers.

- What we build: `docs/architecture.md`
- Build plan: `docs/phases.md`
- Settled decisions: `docs/decisions.md`
- Rules for Claude Code: `CLAUDE.md`

## Requirements (dev)
- macOS, Docker Desktop, uv, Node 20+, Ollama
- `ollama pull qwen3-vl:4b`
- Xero developer app (Client ID/Secret) connected to the Xero Demo Company

## Run
```bash
cp .env.example .env      # fill in values
ollama serve              # separate terminal, native (not Docker)
make up                   # Postgres + Redis
make migrate
make api                  # FastAPI
make worker               # Celery
make web                  # Workbench
```

## Use
1. Put files in `data/inbox/Purchase/<YYYY-MM-DD>/`
2. Open the Workbench → Run → pick folder + date
3. Review, approve → posted to Xero as Awaiting Approval

## Develop
```bash
make lint
make test
```
