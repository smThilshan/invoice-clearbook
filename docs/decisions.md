# Key Decisions

Settled choices. Don't re-argue them in code; change them here first if needed.

| # | Decision | Why | Revisit when |
|---|---|---|---|
| 1 | Everything on Missan's local servers, including OCR and the model; no cloud, no third-party LLM APIs | Client financial data must not leave Missan's servers | Never, for client data |
| 2 | **Qwen3-VL-4B-Instruct** for the whole system, dev through production; PaddleOCR alongside | Testing done; small enough for dev Mac and a modest GPU; dev and prod results stay close | Fine-tuned adapter can't reach accuracy targets |
| 3 | Ollama (native) on dev, vLLM on prod, behind one `ExtractionClient` | Fast local dev; production throughput; switch by config | — |
| 4 | Typed date folders as capture; folder declares, system verifies | Accountant already knows the type; free labels | Email capture needed |
| 5 | Review Workbench in our system; Approve posts to Xero | One place for all invoice work; only verified data in Xero; exact corrections | Missan insists on working only in Xero (fallback: post drafts) |
| 6 | Post as `SUBMITTED` in MVP and pilot, `AUTHORISED` by rule later | Extra gate while trust is built | Accuracy proven in pilot |
| 7 | Outbox + idempotent, resumable posting | No lost approvals, no double posting | — |
| 8 | Rule-based validation, never LLM-as-judge | Predictable, explainable, testable, auditable | — |
| 9 | Python backend (FastAPI, Celery, Postgres) | One language and one schema with the AI stack | Team standard changes |
| 10 | Post data only to Xero, no attachment; Missan's folders are the official archive | Simple, cheap, reliable | Finance requires attachments in Xero |
| 11 | Canonical invoice model; Xero logic only in an adapter | Other accounting systems later without rebuild | Second accounting system needed |
| 12 | Fine-tune only on evidence, LoRA per client | Cheap fixes first; reversible adapters | Consistent error patterns |
| 13 | Monorepo: `backend/` + `workbench/` + `docs/` + `infra/` | One place, shared docs, simple for one engineer + Claude Code | Team grows |
| 14 | MVP in 3 weeks: functional first (Phases 1–5), non-functional later | Deliver working value fast; harden after | — |
| 15 | MVP storage = local filesystem behind `Storage` interface; MinIO later | Fewer moving parts now; no rewrite later | Phase 9 |
| 16 | MVP document types: bills, supplier credit notes, receipts | Covers the bulk of purchase-side work | Phase 11 |
