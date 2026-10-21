"""Corrections (task 4.7): every field where the accountant's version differs from the model's.

The model's side is its last extraction (`extractions.data`) and, for line account and tax
codes, the coding step's suggestion (`documents.coding`): so suggestions are measured too. One
row per document and field (unique), kept in step with every save: a field changed again is
updated, a field set back to the model's value is removed. At approval (task 4.8) the rows are
the final corrections, with the supplier, model and prompt version, for accuracy reports and
later learning (Phase 10).

Values are compared as values (1050 equals 1050.00) and stored as JSON (money as exact text,
dates as ISO). Lines are matched by position: removing line 1 shifts the lines after it.
"""

import json
from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.invoice import Invoice, Line
from app.models import Correction, Document, Extraction
from app.pipeline.verify.step import working_copy

HEADER_FIELDS = tuple(name for name in Invoice.model_fields if name not in ("lines", "evidence"))
LINE_VALUE_FIELDS = ("description", "qty", "unit_price", "amount")
LINE_CODE_FIELDS = ("account_code", "tax_type")  # suggested by coding, not extracted


def sync_corrections(session: Session, document: Document, *, corrected_by: str) -> None:
    """Make the document's correction rows match its current working copy. Runs in the
    caller's transaction."""
    extraction = session.scalars(
        select(Extraction)
        .where(Extraction.document_id == document.id)
        .order_by(Extraction.attempt.desc())
        .limit(1)
    ).first()
    model = _values(_model_invoice(extraction), document.coding or [])
    final = _values(working_copy(document), None)

    differing = {
        field: (model.get(field), final.get(field))
        for field in model.keys() | final.keys()
        if model.get(field) != final.get(field)
    }
    existing = {
        row.field: row
        for row in session.scalars(select(Correction).where(Correction.document_id == document.id))
    }
    for field, row in existing.items():
        if field not in differing:
            session.delete(row)  # back to the model's value: not a correction any more
    for field, (model_value, final_value) in differing.items():
        correction = existing.get(field)
        if correction is None:
            correction = Correction(
                client_id=document.client_id, document_id=document.id, field=field
            )
            session.add(correction)
        correction.extraction_id = extraction.id if extraction else None
        correction.model_value = _json(model_value)
        correction.final_value = _json(final_value)
        correction.vendor_key = document.vendor_key
        correction.model = extraction.model if extraction else None
        correction.prompt_version = extraction.prompt_version if extraction else None
        correction.corrected_by = corrected_by
    session.flush()


def corrections_of(session: Session, document: Document) -> list[Correction]:
    return list(
        session.scalars(
            select(Correction)
            .where(Correction.document_id == document.id)
            .order_by(Correction.field)
        )
    )


def _model_invoice(extraction: Extraction | None) -> Invoice | None:
    if extraction is None or extraction.data is None:
        return None  # the answer could not be read: every value was entered by hand
    try:
        return Invoice.model_validate(extraction.data)
    except ValidationError:
        return None


def _values(invoice: Invoice | None, coding: list[dict[str, Any]] | None) -> dict[str, Any]:
    """Field path -> value, None-valued fields left out. `coding` given: line codes come from
    the coding suggestions (the model's side); None: from the lines themselves."""
    if invoice is None:
        return {}
    values: dict[str, Any] = {name: getattr(invoice, name) for name in HEADER_FIELDS}
    for index, line in enumerate(invoice.lines):
        values |= {f"lines[{index}].{name}": getattr(line, name) for name in LINE_VALUE_FIELDS}
        values |= _line_codes(index, line, coding)
    return {field: value for field, value in values.items() if value is not None}


def _line_codes(index: int, line: Line, coding: list[dict[str, Any]] | None) -> dict[str, Any]:
    if coding is None:
        return {f"lines[{index}].{name}": getattr(line, name) for name in LINE_CODE_FIELDS}
    suggested = coding[index] if index < len(coding) else {}
    tax_type = suggested.get("tax_type")
    return {
        f"lines[{index}].account_code": suggested.get("account_code"),
        f"lines[{index}].tax_type": tax_type if tax_type is None else str(tax_type),
    }


def _json(value: Any) -> Any:
    """Exact JSON: Decimal and date as text, enums as their value."""
    if value is None:
        return None
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Decimal | date):
        return str(value) if isinstance(value, Decimal) else value.isoformat()
    return json.loads(json.dumps(value))
