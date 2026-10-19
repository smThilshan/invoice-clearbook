# Architecture — Invoice Automation for Missan

Source of truth for WHAT we build. Rules for HOW we build are in `/CLAUDE.md`.
Sections marked **[MVP]** are built now. **[LATER]** sections are built after the MVP — do not build them unless asked.

---

## 1. Overview
Accountants stop typing invoices into Xero. The system reads, extracts, checks and codes each document;
an accountant verifies it side by side in the Review Workbench; approval posts the verified data to Xero.
Everything — including the AI model — runs on Missan's own local servers. No client data goes to any third-party API.

Design for growth: `client_id` on every record; all Xero-specific logic behind one adapter.

## 2. Scope
| In scope | Out of scope (for now) |
|---|---|
| Missan, one Xero organisation | Other clients |
| Supplier bills, supplier credit notes, receipts **[MVP]** | Other accounting software |
| Supplier debit notes, sales invoices made outside Xero **[LATER]** | Sales invoices already in Xero |
| Self-hosted OCR + Qwen3-VL-4B | Any third-party LLM API |
| PDF, scanned and photo documents | Peppol e-invoices (later lane) |

## 3. Workflow (accountant's view)
1. **Sort**: files go into typed date folders: `Purchase/2026-10-20/`, `Sales/2026-10-20/`, `Unsorted/2026-10-20/`.
2. **Run**: in the Workbench, pick folder + date, click Run.
3. **Review**: Ready / Check / Exception queues; fix flagged fields; approve or reject.
4. **Done**: approved data posts to Xero.

## 4. Pipeline
Each file is one job. Each step saves its result and sets a status.

| # | Step | What happens | Status | Scope |
|---|---|---|---|---|
| 1 | Run | List new files in the chosen folder/date; skip already-seen | — | MVP |
| 2 | Ingest | Stable-file check, type/size check, SHA-256 hash (dup check 1), copy via `Storage`, DB row with direction + period | `CAPTURED` | MVP (ClamAV LATER) |
| 3 | Read | Digital PDF → PyMuPDF text layer. Scan/photo → OpenCV cleanup + PaddleOCR (text + word boxes). Page images saved | `READ` | MVP (multi-invoice split + content fingerprint LATER) |
| 4 | Extract | Qwen3-VL-4B gets page image(s) + OCR text + prompt + JSON schema → canonical `Invoice`, temperature 0 | `EXTRACTED` | MVP |
| 5 | Verify | Validation rules, account/tax coding, business-key dup check (3) → Ready / Check / Exception | `VERIFIED` → `IN_REVIEW` | MVP (dup 4, fraud LATER) |
| 6 | Review | Accountant fixes and approves in Workbench; corrections saved | `APPROVED` / `REJECTED` | MVP (four-eyes LATER) |
| 7 | Post | Outbox → worker → Xero lookup (dup 5) → create contact if new → create document with idempotency key → save Xero ID | `POSTING` → `POSTED` | MVP |
| 8 | Sync | Poll Xero every 15 min for status/payment/void; daily reconciliation | `SYNCED` / `VOIDED` | LATER |
| 9 | Learn | Vendor mappings update on approval; corrections feed prompts; fine-tuning | — | Vendor mapping MVP, rest LATER |

## 5. Document states
```
CAPTURED → READ → EXTRACTED → VERIFIED → IN_REVIEW → APPROVED → POSTING → POSTED → SYNCED
IN_REVIEW → REJECTED
POSTING → POST_ERROR → IN_REVIEW
POSTED/SYNCED → VOIDED  (LATER, via sync)
Side states: DUPLICATE, NON_POSTABLE, FAILED
```
- All transitions go through `domain/state_machine.transition()`, which validates the move and writes `audit_log`.
- Only `APPROVED` can move to `POSTING` — enforced in code AND by a DB constraint/trigger.

## 6. Document types → Xero
We never write debits/credits. Correct document type + account + tax code per line lets Xero create the entries.

| Canonical type | Meaning | Xero object | Scope |
|---|---|---|---|
| `BILL` | Missan owes a supplier | Invoice, Type `ACCPAY` | MVP |
| `CREDIT_NOTE` | Supplier reduces what Missan owes | CreditNote `ACCPAYCREDIT`, allocated to original | MVP |
| `RECEIPT` | Already paid by card/cash | BankTransaction `SPEND` | MVP |
| `DEBIT_NOTE` | Supplier charges more | Extra bill (configurable) | LATER |
| `SALES_INVOICE` | Customer owes Missan (made outside Xero) | Invoice `ACCREC` | LATER |
| `NON_POSTABLE` | Quote, PO, proforma, statement, delivery note, remittance | Not posted; archived | MVP (detect + park) |

Type decision: the folder declares direction/type; content signals confirm (English + Arabic titles, reference to an original invoice, paid marks). Disagreement → Exception, never silent posting.
Xero type codes exist ONLY in `adapters/xero/`.

## 7. Canonical schema (`app/domain/invoice.py`)
```python
class Line(BaseModel):
    description: str
    qty: Decimal | None
    unit_price: Decimal | None
    amount: Decimal
    account_code: str | None      # limited at runtime to Missan's active Xero accounts
    tax_type: TaxType | None      # STANDARD_5, ZERO, EXEMPT, REVERSE_CHARGE, NONE

class Invoice(BaseModel):
    # always present
    document_type: DocumentType   # BILL, CREDIT_NOTE, DEBIT_NOTE, RECEIPT, SALES_INVOICE, NON_POSTABLE
    vendor_name: str
    invoice_number: str
    invoice_date: date
    total: Decimal
    currency: str
    # nullable: null, never invented
    vendor_trn: str | None
    recipient_name: str | None
    recipient_trn: str | None
    due_date: date | None
    po_number: str | None
    original_invoice_ref: str | None   # credit/debit notes
    amount_type: AmountType | None     # INCLUSIVE, EXCLUSIVE, NO_TAX
    subtotal: Decimal | None
    vat: Decimal | None
    lines: list[Line]
    evidence: dict[str, str]           # source text for key values (grounding)
```
Rules: required = only what every real invoice has; enums for fixed choices; money is `Decimal`;
the JSON schema sent to the model is built at runtime so `account_code` is an enum of active accounts;
schema changes are versioned in Git.

## 8. Validation rules [MVP]
Deterministic Python only. The system flags; it never silently fixes. Errors block approval; warnings show yellow.

| Check | Rule | Severity |
|---|---|---|
| Schema | Pydantic types, enums | Error |
| Grounding | Key values (number, dates, total, VAT, TRN) appear in document text after normalization (digits incl. Arabic-Indic, separators, date formats) | Error |
| Line math | qty × unit_price = amount; lines sum to subtotal (discount/rounding lines allowed) | Warning |
| Totals | subtotal + VAT = total (±0.01) | Error |
| VAT | 5% standard, or valid zero/exempt/reverse-charge case | Error or warning |
| TRN | 15 digits; matches vendor's TRN in Xero if known | Error or warning |
| Dates | Not in the future; due ≥ invoice date | Error |
| Period | Invoice date near folder date; not in Xero locked period | Warning / error |
| Codes | Account + tax codes exist and are active in Xero; bills use expense/asset accounts only | Error |
| Credit notes | Original exists; credit ≤ original | Error |
| Vendor | Match Xero contact by TRN, then pg_trgm fuzzy name | Warning if new |
| Risk | Amount far above vendor norm; bank details changed | LATER |

Result: no errors + no warnings → **Ready**; warnings → **Check**; errors / duplicate / type conflict → **Exception**.

## 9. Account and tax coding
Cheapest reliable source first; the model can only choose real codes.
1. **Vendor mapping** — learned from approved history (vendor → account + tax). [MVP: update after 2–3 consistent approvals]
2. **Missan rules** — accountant-editable keyword rules. [LATER]
3. **Qwen3-VL-4B suggestion** — constrained to active accounts. [MVP]
4. **Fine-tuned LoRA adapter.** [LATER]
Every suggestion stores its source and confidence; the Workbench shows it.

UAE VAT tax rules: UAE vendor with TRN + 5% → standard; valid 0% → zero-rated; exempt supply → exempt;
foreign vendor without UAE VAT → reverse charge; no TRN + no VAT → no VAT.
Codes map to Missan's Xero tax rates (synced, never hardcoded). Inclusive/exclusive sent as Xero line amount type.

## 10. Duplicate protection
| # | Where | Check | Scope |
|---|---|---|---|
| 1 | Ingest | SHA-256 file hash | MVP |
| 2 | Read | Normalized content fingerprint | LATER |
| 3 | Verify | Business key: vendor + normalized number + type (Postgres unique constraint) | MVP |
| 4 | Verify | Same vendor + total + dates within a few days | LATER |
| 5 | Pre-post | Xero lookup: contact + invoice number, any status | MVP |
| 6 | Post | Outbox + idempotency key + saved Xero ID | MVP |
| 7 | Sync | Voided/deleted in Xero releases the business key | LATER |

Number normalization: uppercase, strip spaces/symbols, drop leading zeros and common prefixes (`INV-00291`, `inv 291`, `#291` match).
Different vendors may share numbers (vendor in key); credit note may share number with invoice (type in key).

## 11. Review Workbench
**[MVP]**
- Batch queue: status, vendor, number, date, total, issue; sorted Exception → Check → Ready.
- Side-by-side review: original on the left (zoom, pages), extracted form on the right; flagged fields yellow with reason; click field → highlight source (word boxes from OCR).
- Dropdowns of real Xero account/tax codes with source hint; live total recalculation.
- Approve / Reject (reject reasons pick-list); corrections captured at edit time.
- Run screen: pick folder + date, click Run, see progress.

**[LATER]** quick-confirm grid with enforced spot-checks; keyboard shortcuts (Enter, R, Tab, J/K); preload next; four-eyes queue; record locking; create vendor in Xero from Workbench; duplicate side-by-side compare.

## 12. Posting to Xero [MVP]
- On Approve: status `APPROVED` + outbox row in ONE transaction.
- Celery posting worker takes pending outbox rows (`SELECT … FOR UPDATE SKIP LOCKED`).
- Sub-steps, each result saved so a failure resumes from the failed step:
  1. Xero lookup (contact + invoice number, any status)
  2. Create contact if new vendor
  3. Create bill / credit note / bank transaction with idempotency key
  4. Allocate credit note to original
  5. Add history note ("created by automation, reviewed by X")
  6. Save Xero ID → `POSTED`
- MVP posts everything as `SUBMITTED` (Awaiting Approval).
- Errors: temporary (429, timeout, down) → retry with backoff respecting Xero's wait; business (locked period, inactive account) → back to review with Xero's message; token expired → pause posting + alert; unknown → dead-letter.
- Rate limiter in the posting worker (per-organisation limits).

## 13. Sync and reconciliation [LATER]
Scheduled polling every 15 min (If-Modified-Since), no webhooks (no inbound ports on local servers).
Master data (contacts, accounts, tax rates, lock dates) is synced on demand in MVP, scheduled later.
Daily reconciliation: count + total per day vs Xero.

## 14. Storage
- **[MVP]** `Storage` interface backed by the local filesystem (`data/store/`). Originals copied there; page images and OCR output saved.
- **[LATER]** MinIO (encrypted bucket) with lifecycle: `tmp/` 7 days, `originals/` ~90 days after posting, `exceptions/` until resolved + 90 days, `golden/` long-term.
- Metadata (hashes, keys, extracted data) stays in Postgres permanently.
- Xero receives data only, no attachment. Missan's folders remain the official archive.

## 15. Security
- **[MVP]** Everything local; model via local Ollama; secrets in `.env` (not committed); no sensitive values in logs; basic JWT auth with roles (reviewer, approver, admin).
- **[LATER]** Firewall (outbound to Xero only), TLS, disk + bucket encryption, Vault/Docker secrets, SSO/MFA, append-only audit log enforced at DB level, ClamAV, fraud alerts, masked logs in self-hosted Loki.
- Prompt injection: hidden text cannot change outputs beyond the schema; grounding, validation and human review catch the rest.

## 16. Model and serving
| Role | Choice |
|---|---|
| Text reading | PaddleOCR 3.x (pinned), CPU — text + word positions for highlights and grounding |
| Digital PDFs | PyMuPDF text layer |
| Understanding | **Qwen3-VL-4B-Instruct** (pinned revision) — the model for the whole system, dev through production |
| Dev serving | Ollama, native on the 16 GB Mac mini (`qwen3-vl:4b`), JSON schema enforced |
| Prod serving | vLLM on the GPU server, BF16, guided JSON, LoRA adapter serving [LATER] |

- Always send OCR text with the image; cap page images ~1500 px long side.
- If line items are messy, split extraction into header call + lines call.
- `ExtractionClient` hides the difference between Ollama and vLLM structured-output parameters; switching is config only.
- Not used: RAG/vector DBs, third-party LLM APIs, LLM-as-judge, training from scratch.

## 17. Infrastructure
| Env | Setup |
|---|---|
| Dev [MVP] | 16 GB Mac mini: Ollama native; Postgres 16 + Redis in Docker Compose; FastAPI, Celery, Workbench run locally; Xero Demo Company |
| Prod [LATER] | GPU server with one 24–32 GB professional GPU (e.g. L4 / RTX 4000 Ada / RTX 5000 Ada), 16+ cores, 64–128 GB RAM, mirrored NVMe, Ubuntu 22.04 LTS, Docker Compose + NVIDIA Container Toolkit, nightly backups, UPS |

## 18. Tech stack
| Layer | Tools |
|---|---|
| Workbench | React, TypeScript, Vite, react-pdf, React Hook Form, TanStack Query |
| API | FastAPI, Pydantic v2, pydantic-settings, Uvicorn |
| Jobs | Celery + Redis (queues: `pipeline`, `posting`) |
| DB | PostgreSQL 16 + pg_trgm, SQLAlchemy 2.0, Alembic |
| Reading | PyMuPDF, OpenCV, PaddleOCR |
| Extraction | Qwen3-VL-4B via Ollama (dev) / vLLM (prod) |
| Xero | xero-python SDK |
| Auth | JWT with roles |
| Quality | pytest, ruff, mypy, pre-commit |
| Later | MinIO, Langfuse, Prometheus, Grafana, Sentry, Loki, Vault, Ansible, LLaMA-Factory, MLflow |
