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
- [x] **1.7 ExtractionClient** — interface + Ollama implementation (`qwen3-vl:4b-instruct`), structured JSON output; smoke test

Done when: `make up && make migrate && make api` works, master data is in DB, model returns schema-valid JSON.

## Phase 2 — Core pipeline (Day 3–5)
Goal: a folder of invoices becomes structured data in Postgres.

- [x] **2.1 Storage interface** — local filesystem implementation
- [x] **2.2 Celery setup** — `pipeline` and `posting` queues; `make worker`
- [x] **2.3 Run + Ingest** — `POST /runs {folder, date}`; list files, stable-file + type/size check, SHA-256, skip seen, create document (`CAPTURED`), enqueue one job per file
- [x] **2.4 Read** — PyMuPDF text layer for digital PDFs; render pages + OpenCV + PaddleOCR for scans; save text, word boxes, page images (`READ`)
- [x] **2.5 Extract** — flat model schema without line coding (account/tax codes are task 3.6); prompt with image(s) + page text; call ExtractionClient; save extraction (`EXTRACTED`)
- [x] **2.6 Pipeline chaining + errors** — ingest → read → extract chained; failures set `FAILED` with reason; tasks safe to re-run
- [x] **2.7 Run status API** — `GET /runs/{id}` with per-document status

Done when: drop invoices in `data/inbox/Purchase/<date>/`, call Run, all reach `EXTRACTED`.

## Phase 3 — Verify (Day 6–7)
Goal: every document has codes and a clear status with reasons.

- [x] **3.1 Normalizers** — numbers (incl. Arabic-Indic digits, separators), dates, invoice-number normalization
- [x] **3.2 Validation rules** — one function per rule from architecture §8, each returns issues with field + severity + reason; unit tests per rule
- [x] **3.3 Grounding** — key values checked against document text
- [x] **3.4 Duplicate check** — business key unique constraint; duplicates → Exception
- [x] **3.5 Vendor match** — TRN first, then pg_trgm fuzzy name against master-data contacts
- [x] **3.6 Coding** — vendor mapping → Qwen suggestion; store source + confidence per line
- [x] **3.7 Type check** — model's type vs title-area content signals (purchase-side by default, decision 25); conflict → Exception; non-postables parked
- [x] **3.8 Verify step** — run all above, set Ready / Check / Exception, move to `IN_REVIEW`
- [x] **3.9 Clients & folder selection** (decision 25) — `clients` table (name, shared-drive inbox folder, accounting provider); `POST /runs {client, folder}` for any folder inside the client's folder (sub-folders included, path kept inside it); `GET /clients/{id}/folders` for the picker; folder date read from the folder name when possible, else none (`folder_date` nullable); replaces the fixed `Purchase/<date>` layout

Done when: every extracted document has issues, codes and a queue category.

## Phase 4 — Review Workbench (Day 8–10)
Goal: an accountant reviews and approves without touching the DB.

- [x] **4.1 Workbench skeleton** — Vite + React + TS (strict), shadcn/ui (Tailwind + Radix), routing, typed API client generated from the backend's OpenAPI, TanStack Query, basic layout, system status; no login yet (decision 26)
- [x] **4.2 Backend review API** — master data CSV upload (from 1.5) and code options per client; queue (Exception → Check → Ready, filters, paging), document detail with page images and word boxes, update fields (edited/confirmed fields become human-verified, decision 24; all checks re-run without the model; type change parks/un-parks), reject with a reason pick-list; approve is 4.8
- [x] **4.3 Run screen** — pick restaurant, browse folders (sub-folders included), confirm date (from the folder name; cleared = no folder date), Run, live progress per file at `/run/<id>`
- [x] **4.4 Queue screen** — table sorted Exception → Check → Ready; filters (restaurant, category, in review / parked, one run) kept in the address; paging; last restaurant remembered per computer
- [x] **4.5 Review screen** — page viewer left (page pictures, zoom), form right (header + lines), flagged fields with reason (errors red, warnings yellow), printed-value suggestions, "checked against the original" tick, code dropdowns with source hint, live totals (exact arithmetic), save & re-check, reject with reason, unsaved-changes warning (decision 32)
- [x] **4.6 Field highlight** — focus a field → its value is highlighted where printed (all places, any page; follows typing); matched in the backend like grounding (`GET /documents/{id}/locate`); names found by most of their words shown dashed; "not found in the page text" otherwise
- [x] **4.7 Corrections** — one row per changed field (model value or suggested code vs final value, supplier, model, prompt version), kept in step with every save (unique per document + field); "AI read: …" shown under changed fields; reject reason pick-list (done in 4.2/4.5)
- [x] **4.8 Approve flow** — server re-validates (no model); errors block, warnings do not; `APPROVED` + outbox row + corrections + supplier mapping in one transaction; approve takes the document as last saved (button disabled with unsaved edits or errors); one consistent approval more for the same account/VAT, a different choice starts over, mixed accounts teach nothing
- [x] **4.9 Step timings** (decision 33) — each step records its work time; run page shows per file waiting / read / extract (model) / coding (model) / checks / total and the average per invoice without waiting; review screen shows processing and model time

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
**First: login & roles (decision 26)** — `users` table (Missan staff, access to all restaurants), Argon2-hashed passwords, JWT with expiry, roles reviewer / approver / admin, `make add-user`, every endpoint except health requires login, approve requires approver; then the API may listen beyond localhost.
GPU server + vLLM serving Qwen3-VL-4B; Missan's real accounting system connected; daily use, all `SUBMITTED`.
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
