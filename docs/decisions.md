# Key Decisions

Settled choices. Don't re-argue them in code; change them here first if needed.

| # | Decision | Why | Revisit when |
|---|---|---|---|
| 1 | Everything on Missan's local servers, including OCR and the model; no cloud, no third-party LLM APIs | Client financial data must not leave Missan's servers | Never, for client data |
| 2 | **Qwen3-VL-4B-Instruct** for the whole system, dev through production; PaddleOCR alongside | Testing done; small enough for dev Mac and a modest GPU; dev and prod results stay close | Fine-tuned adapter can't reach accuracy targets |
| 3 | Ollama (native) on dev, vLLM on prod, behind one `ExtractionClient` | Fast local dev; production throughput; switch by config | — |
| 4 | Typed date folders as capture; folder declares, system verifies | Accountant already knows the type; free labels | Email capture needed |
| 5 | Review Workbench in our system; Approve posts to the accounting system | One place for all invoice work; only verified data in the accounting system; exact corrections | Missan insists on working only in Xero (fallback: post drafts) |
| 6 | Xero adapter posts as `SUBMITTED` in MVP and pilot, `AUTHORISED` by rule later | Extra gate while trust is built | Accuracy proven in pilot |
| 7 | Outbox + idempotent, resumable posting | No lost approvals, no double posting | — |
| 8 | Rule-based validation, never LLM-as-judge | Predictable, explainable, testable, auditable | — |
| 9 | Python backend (FastAPI, Celery, Postgres) | One language and one schema with the AI stack | Team standard changes |
| 10 | Post data only to the accounting system, no attachment; Missan's folders are the official archive | Simple, cheap, reliable | Finance requires attachments in Xero |
| 11 | Canonical invoice model; accounting-system logic only in an adapter (extended by 20) | Other accounting systems later without rebuild | Second accounting system needed |
| 12 | Fine-tune only on evidence, LoRA per client | Cheap fixes first; reversible adapters | Consistent error patterns |
| 13 | Monorepo: `backend/` + `workbench/` + `docs/` + `infra/` | One place, shared docs, simple for one engineer + Claude Code | Team grows |
| 14 | MVP in 3 weeks: functional first (Phases 1–5), non-functional later | Deliver working value fast; harden after | — |
| 15 | MVP storage = local filesystem behind `Storage` interface; MinIO later | Fewer moving parts now; no rewrite later | Phase 9 |
| 16 | MVP document types: bills, supplier credit notes, receipts | Covers the bulk of purchase-side work | Phase 11 |
| 17 | Only `document_type` (and line `amount`) required in the schema; "required to post" is a validation rule per type. Bill / credit note: number, date, total, vendor, currency missing → Error. Receipt: number missing → Warning, the rest → Error. Number null but a label like "Invoice No" / "رقم الفاتورة" in the text → flagged as likely missed. Accountant can mark "no number on document" (audited); a number is never generated | A field the model can't find must reach review flagged, not fail extraction; null over invented | Real samples (end-to-end run, Phase 5) show a different pattern |
| 18 | Business key = client + vendor + document type + key, where key is `N:<normalized number>` or, without a number, `F:<invoice date>\|<total>`. Partial unique index ignoring REJECTED, DUPLICATE, FAILED, VOIDED | Receipts often have no number but still need duplicate protection; a rejected document must not block its re-run | False positives on identical same-day receipts become a problem |
| 19 | "Only APPROVED → POSTING" enforced in the DB by a `BEFORE UPDATE OF status` trigger guarding that one edge; the full transition table lives only in `domain/state_machine.py` | DB safety net without two copies of the state machine drifting apart | — |
| 20 | The app runs standalone; accounting systems are optional adapters behind one `AccountingSystem` port, one per client. `manual` adapter (CSV master data in, export file out) needs no connection; Xero is the first API adapter (Phase 5); others (e.g. Zoho Books) only when a client needs them. Master data in neutral tables (`ledger_accounts`, `tax_rates`, `contacts`) with `provider` + `external_id` | Not locked to one vendor; the product works before any connection exists; a new system is one adapter, not a rebuild | A client needs a capability the port can't express |
