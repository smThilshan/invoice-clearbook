"""Answer key (decision 63): documents the accountant marked correct, to measure every change.

In review, the accountant corrects a document against its image, saves it, and marks it
correct: its working copy is kept as the answer, through Storage (on Missan's servers only).
Approving is not needed, so nothing is sent to the accounting system.

A measurement (`make answer-key`) replays each marked document: the model's saved answer (no
model call) through today's code (`repair_answer`, the same steps as extraction) on the saved
page text, and compares the result with the answer, field by field. Only what the reading
decides is compared: account and VAT codes are coding's and the accountant's. The same input
always gives the same result, so only a code change moves the score.
"""

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.llm import ExtractionResult
from app.adapters.storage import NotFoundError, Storage
from app.adapters.storage.keys import answer_key
from app.domain.invoice import Invoice
from app.models import Document, Extraction
from app.pipeline.extract import repair_answer, validate_answer
from app.pipeline.read import load_page_texts
from app.pipeline.verify.step import working_copy

# Compared as values; names without case, spaces and punctuation ("AL SHAHAMA.SUPERMARKET").
FIELDS = ("document_type", "vendor_name", "vendor_trn", "invoice_number", "invoice_date",
          "subtotal", "vat", "total", "amount_type", "lines")  # fmt: skip


class AnswerKeyError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class Saved:
    saved_at: datetime


def save_answer(storage: Storage, document: Document, *, now: datetime | None = None) -> Saved:
    """The document's working copy, as last saved, becomes its answer."""
    invoice = working_copy(document)
    if invoice is None:
        raise AnswerKeyError("this document has no data to keep as the answer: enter it first")
    saved = Saved(now or datetime.now(UTC))
    body = {
        "document_id": str(document.id),
        "source_path": document.source_path,
        "saved_at": saved.saved_at.isoformat(),
        "invoice": json.loads(invoice.model_dump_json()),  # money as exact text
    }
    storage.put(answer_key(document.client_id, document.id), json.dumps(body, indent=1).encode())
    return saved


def saved(storage: Storage, document: Document) -> Saved | None:
    body = _body(storage, document)
    return Saved(datetime.fromisoformat(body["saved_at"])) if body else None


def remove_answer(storage: Storage, document: Document) -> None:
    storage.delete(answer_key(document.client_id, document.id))


def answer_of(storage: Storage, document: Document) -> Invoice | None:
    body = _body(storage, document)
    return Invoice.model_validate(body["invoice"]) if body else None


def replay(session: Session, storage: Storage, document: Document) -> Invoice | None:
    """The model's last answer through today's code, as extraction would make it. None: no
    answer, or one that fails the schema."""
    extraction = session.scalars(
        select(Extraction)
        .where(Extraction.document_id == document.id)
        .order_by(Extraction.attempt.desc())
        .limit(1)
    ).first()
    if extraction is None or extraction.raw_output is None:
        return None
    llm = ExtractionResult(model=extraction.model, raw_output=extraction.raw_output,
                           truncated=False, duration_ms=0, prompt_tokens=None,
                           output_tokens=None)  # fmt: skip
    invoice, _ = validate_answer(llm)
    return repair_answer(invoice, load_page_texts(storage, document)) if invoice else None


def differences(expected: Invoice, got: Invoice | None) -> dict[str, tuple[str, str]]:
    """Field -> (expected, got), for every compared field that differs."""
    return {
        field: (_show(field, expected), _show(field, got))
        for field in FIELDS
        if got is None or _value(field, expected) != _value(field, got)
    }


def _value(field: str, invoice: Invoice) -> Any:
    if field == "lines":
        return sorted(line.amount for line in invoice.lines)  # Decimals: 2.5 equals 2.50
    value = getattr(invoice, field)
    if field in ("vendor_name", "invoice_number") and value is not None:
        return re.sub(r"[\W_]", "", str(value)).upper()
    return value


def _show(field: str, invoice: Invoice | None) -> str:
    if invoice is None:
        return "no answer"
    if field == "lines":
        return " + ".join(str(line.amount) for line in invoice.lines) or "none"
    value = getattr(invoice, field)
    if isinstance(value, Decimal):
        return f"{value:.2f}" if value == value.quantize(Decimal("0.01")) else str(value)
    return "empty" if value is None else str(getattr(value, "value", value))


def _body(storage: Storage, document: Document) -> dict[str, Any] | None:
    try:
        data: dict[str, Any] = json.loads(storage.get(answer_key(document.client_id, document.id)))
    except NotFoundError:
        return None
    return data
