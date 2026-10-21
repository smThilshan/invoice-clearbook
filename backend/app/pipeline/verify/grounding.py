"""Grounding (task 3.3): every key value the model extracted must be printed on the document.

The page text from the Read step is the independent witness (rule 7: the model never checks
itself). Values are compared after normalization (normalize.py), never as raw strings.

OCR pages only: a scan can read "1,050.10" as "1,O5O.1O"; the model, which also sees the image,
may correctly answer 1050.10. So on OCR pages letters that OCR confuses with digits (O 0,
l/I 1, S 5, B 8) next to digits are also read as digits. The exact text layer of digital PDFs
gets no such tolerance.

A value not found is an error (architecture §8), except the due date (often printed as terms
like "Net 30"): a warning. In review, a value the accountant confirms or types counts as
human-verified and is no longer grounded (Phase 4).
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.domain.enums import DocumentType
from app.domain.invoice import Invoice
from app.pipeline.read import PageText
from app.pipeline.verify.issues import Issue, Severity
from app.pipeline.verify.normalize import (
    find_amounts,
    find_dates,
    normalize_trn,
    to_ascii_digits,
)

_AMOUNT_FIELDS = {"total": "total", "subtotal": "subtotal", "vat": "VAT amount"}
_DATE_FIELDS = {
    "invoice_date": ("document date", Severity.ERROR),
    "due_date": ("due date", Severity.WARNING),
}

# Letters OCR mistakes for digits, only when they touch a digit or a number separator.
_OCR_DIGITS = str.maketrans("OoQDlIiSsBZz", "000011155822")
_CONFUSABLE_NEAR_DIGIT = re.compile(r"(?<=[\d.,])[OoQDlIiSsBZz]|[OoQDlIiSsBZz](?=[\d.,])")
_DIGIT_RUN = re.compile(r"\d(?:[\d \-]*\d)?")

# Decision 17: a number label on the page while no number was extracted -> likely missed.
_NUMBER_LABEL = re.compile(
    r"\b(?:invoice|inv|bill|receipt|rcpt|credit\s*note|tax\s*invoice)\s*(?:no|number|num|#)"
    r"|رقم\s*(?:الفاتورة|الإيصال)",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class _Witness:
    """Everything printed on the document, in comparable form."""

    amounts: set[Decimal]
    dates: set[date]
    compact: str  # uppercase letters and digits only, for document numbers
    compact_ocr: str  # same, with OCR digit confusions folded (OCR pages only)
    digit_runs: set[str]  # digit sequences with spaces/dashes removed, for TRNs
    raw: str


def check_grounding(invoice: Invoice, pages: Sequence[PageText]) -> list[Issue]:
    if invoice.document_type is DocumentType.NON_POSTABLE:
        return []  # parked, never posted (same as the validation rules)
    witness = _witness(pages)
    return [
        *_amounts(invoice, witness),
        *_dates(invoice, witness),
        *_number(invoice, witness),
        *_trn(invoice, witness),
    ]


def _witness(pages: Sequence[PageText]) -> _Witness:
    exact = "\n".join(page.text for page in pages)
    ocr = "\n".join(_fold_ocr_digits(page.text) for page in pages if page.source == "ocr")
    both = to_ascii_digits(f"{exact}\n{ocr}")
    return _Witness(
        amounts=find_amounts(both),
        dates=find_dates(both),
        compact=_compact(exact),
        compact_ocr=_compact(ocr).translate(_OCR_DIGITS),
        digit_runs={re.sub(r"\D", "", run) for run in _DIGIT_RUN.findall(both)},
        raw=exact,
    )


def _amounts(invoice: Invoice, witness: _Witness) -> list[Issue]:
    issues = []
    for field, label in _AMOUNT_FIELDS.items():
        value = getattr(invoice, field)
        if value is None or value == 0:  # zero VAT is often left blank
            continue
        if abs(value) not in witness.amounts:
            issues.append(_not_found(field, label, value, Severity.ERROR))
    return issues


def _dates(invoice: Invoice, witness: _Witness) -> list[Issue]:
    issues = []
    for field, (label, severity) in _DATE_FIELDS.items():
        value = getattr(invoice, field)
        if value is not None and value not in witness.dates:
            issues.append(_not_found(field, label, value, severity))
    return issues


def _number(invoice: Invoice, witness: _Witness) -> list[Issue]:
    number = invoice.invoice_number
    if number is None:
        if _NUMBER_LABEL.search(witness.raw):
            return [
                Issue(
                    "number_likely_missed",
                    Severity.WARNING,
                    "invoice_number",
                    "No document number was extracted, but the page has a number label "
                    "(e.g. 'Invoice No'): check the original.",
                )
            ]
        return []
    wanted = _compact(number)
    found = bool(wanted) and (
        wanted in witness.compact or wanted.translate(_OCR_DIGITS) in witness.compact_ocr
    )
    return (
        [] if found else [_not_found("invoice_number", "document number", number, Severity.ERROR)]
    )


def _trn(invoice: Invoice, witness: _Witness) -> list[Issue]:
    if invoice.vendor_trn is None:
        return []
    digits = normalize_trn(invoice.vendor_trn)
    if digits and digits in witness.digit_runs:
        return []
    return [_not_found("vendor_trn", "supplier TRN", invoice.vendor_trn, Severity.ERROR)]


def _fold_ocr_digits(text: str) -> str:
    """'1,O5O.1O' -> '1,050.10'. Repeated, because each fix can expose a neighbour."""
    previous = None
    while previous != text:
        previous = text
        text = _CONFUSABLE_NEAR_DIGIT.sub(lambda m: m.group().translate(_OCR_DIGITS), text)
    return text


def _compact(text: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", to_ascii_digits(text).upper())


def _not_found(field: str, label: str, value: object, severity: Severity) -> Issue:
    return Issue(
        "not_on_document",
        severity,
        field,
        f"The {label} {value} was not found on the document: check it against the original.",
    )
