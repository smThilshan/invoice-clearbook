# Phases

MVP = Phases 1–5. Functional first, polish later.
Each task is one Claude Code session: plan → approve → build → lint + test → commit.
Mark tasks `[x]` when done so `/phase` always knows what's next.

---

## Phase 1 — Foundation (Day 1–2)
Goal: app runs, DB holds master data, model responds. No accounting-system connection needed.

- [x] **1.1 Repo skeleton** — folders per CLAUDE.md, `backend/` uv project, ruff, mypy, pytest, pre-commit, `docker-compose.yml` (Postgres 16 + Redis), `Makefile`, `.env.example`, `README.md`
- [x] **1.2 Core config** — pydantic-settings, DB session, logging, FastAPI app with `/health`
- [x] **1.3 Domain** — `Invoice` + `Line` schema, enums, state machine with allowed transitions; unit tests
- [x] **1.4 DB models + migration** — `documents`, `extractions`, `corrections`, `outbox`, `audit_log`, master data, `vendor_mappings`, `runs`; `client_id` everywhere; unique business key; pg_trgm extension
- [x] **1.4b Accounting-agnostic master data** — neutral `ledger_accounts`, `tax_rates`, `contacts` (`provider` + `external_id`) replace the `xero_*` tables (decision 20)
- [x] **1.5 Master data import** — `AccountingSystem` port (master-data part) + `manual` adapter: CSV import of accounts, tax rates (mapped to canonical `TaxType`), contacts; validate, upsert, report; `make import-master-data` (HTTP endpoint moves to 4.2, behind login)
- [x] **1.6 Sample master data** — fictional F&B sample CSVs (chart of accounts incl. petty cash, UAE VAT rates, suppliers) in `infra/samples/master_data/`; `make seed`
- [x] **1.7 ExtractionClient** — interface + Ollama implementation (`qwen3-vl:4b`), structured JSON output; smoke test

Done when: `make up && make migrate && make api` works, master data is in DB, model returns schema-valid JSON.

## Phase 2 — Core pipeline (Day 3–5)
Goal: a folder of invoices becomes structured data in Postgres.

- [x] **2.1 Storage interface** — local filesystem implementation
- [x] **2.2 Celery setup** — `pipeline` and `posting` queues; `make worker`
- [x] **2.3 Run + Ingest** — `POST /runs {folder, date}`; list files, stable-file + type/size check, SHA-256, skip seen, create document (`CAPTURED`), enqueue one job per file
- [x] **2.4 Read** — PyMuPDF text layer for digital PDFs; render pages + OpenCV + PaddleOCR for scans; save text, word boxes, page images (`READ`)
- [ ] **2.5 Extract** — build runtime JSON schema (active accounts enum); prompt with image(s) + OCR text; call ExtractionClient; save extraction (`EXTRACTED`)
- [ ] **2.6 Pipeline chaining + errors** — ingest → read → extract chained; failures set `FAILED` with reason; tasks safe to re-run
- [ ] **2.7 Run status API** — `GET /runs/{id}` with per-document status

Done when: drop invoices in `data/inbox/Purchase/<date>/`, call Run, all reach `EXTRACTED`.

## Phase 3 — Verify (Day 6–7)
Goal: every document has codes and a clear status with reasons.

- [ ] **3.1 Normalizers** — numbers (incl. Arabic-Indic digits, separators), dates, invoice-number normalization
- [ ] **3.2 Validation rules** — one function per rule from architecture §8, each returns issues with field + severity + reason; unit tests per rule
- [ ] **3.3 Grounding** — key values checked against document text
- [ ] **3.4 Duplicate check** — business key unique constraint; duplicates → Exception
- [ ] **3.5 Vendor match** — TRN first, then pg_trgm fuzzy name against master-data contacts
- [ ] **3.6 Coding** — vendor mapping → Qwen suggestion; store source + confidence per line
- [ ] **3.7 Type check** — folder vs content signals; conflict → Exception; non-postables parked
- [ ] **3.8 Verify step** — run all above, set Ready / Check / Exception, move to `IN_REVIEW`

Done when: every extracted document has issues, codes and a queue category.

## Phase 4 — Review Workbench (Day 8–10)
Goal: an accountant reviews and approves without touching the DB.

- [ ] **4.1 Workbench skeleton** — Vite + React + TS, routing, API client, TanStack Query, basic layout, login (JWT)
- [ ] **4.2 Backend review API** — master data import endpoint (from 1.5); list queue, get document (data + issues + page images + word boxes), update fields, approve, reject; JWT roles
- [ ] **4.3 Run screen** — pick folder + date, Run, progress
- [ ] **4.4 Queue screen** — table sorted Exception → Check → Ready, filters
- [ ] **4.5 Review screen** — PDF/image viewer left, form right (header + lines), flagged fields yellow with reason, code dropdowns with source hint, live totals
- [ ] **4.6 Field highlight** — click field → highlight word box on the page
- [ ] **4.7 Corrections** — every edit saved (field, model value, final value, vendor, model/prompt version); reject reason pick-list
- [ ] **4.8 Approve flow** — server re-validates; errors block; `APPROVED` + outbox row in one transaction; vendor mapping updated

Done when: full review and approval works from the browser.

## Phase 5 — Post to an accounting system + complete (Day 11–15)
Goal: folder → review → posted works for bills, credit notes and receipts, with no connection (export) and with Xero.
**MVP complete.**

- [ ] **5.1 Posting worker + port** — `AccountingSystem` posting methods; outbox pickup (`FOR UPDATE SKIP LOCKED`), resumable sub-steps, status `POSTING`, dispatch to the client's adapter
- [ ] **5.2 Manual adapter: export** — approved documents → export file (CSV/JSON) in `data/export/`; `POSTED` with export reference
- [ ] **5.3 Xero adapter: auth** — OAuth2 flow, token storage + refresh, connect to Demo Company
- [ ] **5.4 Xero adapter: master data sync** — accounts, tax rates, contacts → the same neutral tables; `POST /master-data/sync`
- [ ] **5.5 Xero bills** — lookup (contact + number), create contact if new, create ACCPAY `SUBMITTED` with idempotency key, history note, save external ID → `POSTED`
- [ ] **5.6 Xero error handling** — retry/backoff for 429/timeouts, business errors → `POST_ERROR` back to review with Xero message, token expiry pauses posting, rate limiter
- [ ] **5.7 Xero credit notes** — ACCPAYCREDIT + allocation to original
- [ ] **5.8 Xero receipts** — BankTransaction SPEND
- [ ] **5.9 Workbench posting status** — posted / exported / error shown, with link to the document in the accounting system
- [ ] **5.10 End-to-end run** — real sample invoices through the full flow, manual and Xero; fix what breaks
- [ ] **5.11 README + demo script**

Done when: approved documents export without a connection and appear in Xero Awaiting Approval when connected;
retries never double-post.

---

## After the MVP

### Phase 6 — Go live on Missan's setup (Week 4)
GPU server + vLLM serving Qwen3-VL-4B; Missan's real accounting system connected; users and roles; daily use, all `SUBMITTED`.
Done when: real invoices flow daily on the production server.

### Phase 7 — Safety and control (Week 5–6)
Duplicate checks 2, 4, 7; fraud alerts (bank details changed, same number different amount); four-eyes above limit;
record locking; full audit log; accounting-system sync every 15 min + daily reconciliation.
Done when: nothing can double-post, go missing or slip through unchecked.

### Phase 8 — Accountant speed (Week 6–7)
Quick-confirm grid with spot-checks; keyboard shortcuts; preload + auto-advance; create vendor from Workbench;
editable coding rules; normal documents post `AUTHORISED`, high amounts and new vendors stay `SUBMITTED`.
Done when: review takes seconds per invoice.

### Phase 9 — Production hardening (Week 7–8)
MinIO + retention; Vault/Docker secrets, firewall, encryption, ClamAV; Sentry, Prometheus + Grafana, Langfuse, alerts;
nightly backups; runbooks.
Done when: runs unattended and problems are caught before accountants notice.

### Phase 10 — Learning and fine-tuning (Month 3+)
Few-shot examples from same vendor; error dashboard by field and vendor; LoRA fine-tuning of Qwen3-VL-4B
once 300–500 approved documents exist; shadow → canary → full release.
Done when: straight-through rate keeps rising.

### Phase 11 — Expand (by business need)
Sales invoices outside the accounting system, debit notes, email capture, Peppol, more clients (LoRA per client),
other accounting systems (e.g. Zoho Books) as adapters.
