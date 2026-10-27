"""Extract (architecture §4, step 4): page images + page text -> canonical Invoice.

The model sees the images (layout) and the text from the Read step (exact characters), and
answers in the flat model schema (decision 21). Every answer is kept as an `extractions` row,
never edited. A valid answer becomes the document's working copy (`documents.invoice`); an
invalid one is kept with its error and the document still moves on, to be flagged in review
(rule 8: flag, never fix). Model/server failures raise ExtractionError so the task retries.

The prompt does not say which inbox folder the file came from: the type check (task 3.7)
compares the folder with what the content says, so the model must not be told the answer.
"""

import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Any, get_args

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.adapters.llm import ExtractionClient, ExtractionRequest, ExtractionResult
from app.adapters.storage import Storage
from app.adapters.storage.keys import page_image_key
from app.core.config import get_settings
from app.domain.enums import DocumentStatus
from app.domain.invoice import INVOICE_SCHEMA_VERSION, Invoice
from app.domain.state_machine import transition
from app.models import Document, Extraction
from app.pipeline.descriptions import clean_descriptions
from app.pipeline.line_totals import repair_unit_prices
from app.pipeline.model_schema import build_model_schema
from app.pipeline.no_vat import clear_unprinted_vat
from app.pipeline.printed_summary import apply_printed_summary
from app.pipeline.read import PageText, load_page_texts
from app.pipeline.verify.normalize import parse_date
from app.pipeline.verify.vat_type import vat_type_from_amounts

logger = logging.getLogger(__name__)

ACTOR = "system:extract"

# Bump when the prompt or the extraction schema changes; stored with every extraction.
# v2: currency signs (UAE dirham sign introduced 2025, after most model training data).
# v3: dates copied as printed, converted by our parser (decision 28).
# v4: where each TRN is and what is not a TRN (decision 29).
PROMPT_VERSION = "extract-v4"

EXTRACTION_SCHEMA = build_model_schema(line_coding=False)

SYSTEM_PROMPT = """You extract data from one supplier document into JSON that matches the schema.
Rules:
- Use only what is printed on the document. If a value is not on it, use null. Never guess.
- The page text below was read from the document (PDF text layer or OCR). Prefer it for exact
  characters and numbers; use the images for layout and to correct obvious OCR mistakes.
- Dates exactly as printed (e.g. 4/5/2026): do not reorder or convert them.
- Amounts as plain numbers: no currency symbols, no thousands separators.
- document_type: BILL = supplier tax invoice or bill; CREDIT_NOTE = supplier credit note;
  RECEIPT = proof of a payment already made (card/cash); NON_POSTABLE = quote, purchase order,
  proforma, statement, delivery note or remittance.
- amount_type: INCLUSIVE if line amounts include VAT, EXCLUSIVE if VAT is added on top, NO_TAX if
  no VAT applies.
- currency as a 3-letter code. The UAE dirham sign (a letter D crossed by two horizontal bars,
  printed before the amount; OCR may show it as D) means AED. The Saudi riyal sign means SAR.
  "Dh", "Dhs" and the Arabic dirham abbreviation also mean AED.
- TRN (UAE tax registration number) is always exactly 15 digits. It may be labelled TRN,
  VAT Registration No or Tax Registration No. vendor_trn is the seller's, usually in the header
  near its name; recipient_trn is the buyer's, in the customer / bill-to block. Never use an
  excise, licence, phone or customer account number as a TRN. If a TRN is not printed, use null.
- original_invoice_ref only for credit or debit notes that refer to an earlier invoice.
- evidence: for invoice_number, invoice_date, total, vat and vendor_trn, copy the exact text you
  read each value from."""


class ExtractOutcome(StrEnum):
    EXTRACTED = "extracted"  # valid Invoice
    INVALID = "invalid"  # answer kept, failed the schema: flagged in review
    SKIPPED = "skipped"  # not READ


@dataclass(frozen=True, slots=True)
class ExtractResult:
    outcome: ExtractOutcome
    attempt: int = 0
    llm: ExtractionResult | None = None


def build_request(pages: list[PageText], images: list[bytes]) -> ExtractionRequest:
    """The model request for one document (also used by the smoke test)."""
    texts = [page for page in pages if page.text.strip()]
    if not texts:
        prompt = "Extract the document in the image(s)."
    else:
        body = "\n".join(f'<page number="{p.page}">\n{p.text}\n</page>' for p in texts)
        prompt = f"Extract the document. Text of each page, in reading order:\n{body}"
    if len(images) < len(pages):
        prompt += f"\n\nImages are attached for the first {len(images)} of {len(pages)} pages."
    return ExtractionRequest(
        prompt=prompt, json_schema=EXTRACTION_SCHEMA, images=images, system_prompt=SYSTEM_PROMPT
    )


# Invoice fields holding a date; the model writes them as printed text (decision 28).
DATE_FIELDS = tuple(
    name
    for name, field in Invoice.model_fields.items()
    if field.annotation is date or date in get_args(field.annotation)
)


def validate_answer(llm: ExtractionResult) -> tuple[Invoice | None, str | None]:
    """(invoice, None) or (None, error). Errors name fields and types, never values."""
    if llm.truncated:
        return None, "answer cut off at the output token limit"
    try:
        answer = json.loads(llm.raw_output, parse_float=Decimal)  # money stays exact (rule 1)
    except ValueError:
        answer = None
    try:
        if isinstance(answer, dict):
            invoice = Invoice.model_validate(parse_answer_dates(clear_null_text(answer)))
            # Decision 34: the amounts decide the VAT type, not the model's guess. The model is
            # still asked so its prompt stays unchanged: any wording change shifts other fields.
            invoice = invoice.model_copy(update={"amount_type": vat_type_from_amounts(invoice)})
            # Decision 44: a unit price that is really the line total, when proven. Before the
            # descriptions are cleaned: it reads the "N x" they start with.
            if get_settings().repair_unit_prices:
                invoice = repair_unit_prices(invoice)[0]
            # Decision 42: item codes and "5 x" prefixes out of the descriptions, when proven.
            return clean_descriptions(invoice)[0], None
        return Invoice.model_validate_json(llm.raw_output), None  # reports why it is invalid
    except ValidationError as exc:
        problems = ", ".join(f"{'.'.join(map(str, e['loc'])) or 'root'}:{e['type']}"
                             for e in exc.errors())  # fmt: skip
        return None, f"{exc.error_count()} schema error(s): {problems}"


# The model sometimes writes null as text ("recipient_trn": "null"); the checks then took it
# for a value (decision 57). Never a real value on a document.
_NULL_TEXT = frozenset({"null", "none", "n/a"})


def clear_null_text(value: Any) -> Any:
    """Null written as text -> null, in every field and line. The evidence is the text copied
    from the page: kept as written."""
    if isinstance(value, str):
        return None if value.strip().lower() in _NULL_TEXT else value
    if isinstance(value, dict):
        return {key: item if key == "evidence" else clear_null_text(item)
                for key, item in value.items()}  # fmt: skip
    if isinstance(value, list):
        return [clear_null_text(item) for item in value]
    return value


def parse_answer_dates(answer: dict[str, Any]) -> dict[str, Any]:
    """Printed dates -> ISO dates, read day-first as in the UAE ("4/5/2026" = 4 May 2026).
    Text that holds no date becomes null: missing, flagged by the checks, never guessed."""
    converted = dict(answer)
    for field in DATE_FIELDS:
        value = converted.get(field)
        if isinstance(value, str):
            reading = parse_date(value)
            converted[field] = reading.value.isoformat() if reading else None
    return converted


def extract_document(
    session: Session,
    storage: Storage,
    client: ExtractionClient,
    document: Document,
    *,
    max_images: int,
    should_stop: Callable[[], bool] | None = None,
) -> ExtractResult:
    """`should_stop` (task 4.20): asked while the model reads; True abandons the call with
    ExtractionStopped before anything is saved."""
    if document.status is not DocumentStatus.READ:
        return ExtractResult(ExtractOutcome.SKIPPED)

    started = time.perf_counter()
    pages = load_page_texts(storage, document)
    images = [
        storage.get(page_image_key(document.client_id, document.id, number))
        for number in range(1, min(len(pages), max_images) + 1)
    ]
    request = build_request(pages, images)
    # ExtractionError -> the task retries; ExtractionStopped -> the user force-stopped the run
    llm = client.extract(request, should_stop=should_stop)
    invoice, error = validate_answer(llm)
    if invoice is not None:
        # Decision 56: summary rows copied as items, and the totals, from the printed summary.
        invoice = apply_printed_summary(invoice, pages)
        # Decision 52: no VAT on what is not a tax invoice.
        invoice = clear_unprinted_vat(invoice, pages)

    attempt = (
        1 + session.scalars(select(func.count()).where(Extraction.document_id == document.id)).one()
    )
    data = json.loads(invoice.model_dump_json()) if invoice else None  # money as exact strings
    session.add(
        Extraction(
            client_id=document.client_id,
            document_id=document.id,
            attempt=attempt,
            model=llm.model,
            prompt_version=PROMPT_VERSION,
            schema_version=INVOICE_SCHEMA_VERSION,
            raw_output=llm.raw_output,
            data=data,
            error=error,
            duration_ms=llm.duration_ms,
        )
    )
    document.invoice = data
    document.document_type = invoice.document_type if invoice else None

    transition(
        session,
        document,
        DocumentStatus.EXTRACTED,
        actor=ACTOR,
        reason=error,
        details={
            "attempt": attempt,
            "valid": invoice is not None,
            "model": llm.model,
            "prompt_version": PROMPT_VERSION,
            "images": len(images),
            "prompt_tokens": llm.prompt_tokens,
            "output_tokens": llm.output_tokens,
            "reading_ms": llm.reading_ms,
            "writing_ms": llm.writing_ms,
            "work_ms": round((time.perf_counter() - started) * 1000),  # step timings (4.9)
        },
    )
    outcome = ExtractOutcome.EXTRACTED if invoice else ExtractOutcome.INVALID
    return ExtractResult(outcome, attempt, llm)
