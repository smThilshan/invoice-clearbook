"""Duplicate check (task 3.4, decision 18) and credit-note originals (architecture §8).

The file hash at ingest only catches identical files. The business key catches the same
document arriving as a different file (a photo and a PDF of one bill, a resend):

    client + document type + vendor_key + business_key
    vendor_key   = "TRN:<15 digits>" if a valid TRN is printed, else "NAME:<normalized name>"
    business_key = "N:<normalized number>", or "F:<date>|<total>" for documents without one

The unique index on documents (migration 1.4) only counts live documents: rejected, failed,
duplicate and voided ones release their key, so a corrected re-run is never blocked. A
duplicate goes to review as an error; the accountant rejects it with reason "duplicate".
"""

import re
from dataclasses import dataclass
from decimal import Decimal
from pathlib import PurePosixPath

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.enums import DocumentType
from app.domain.invoice import Invoice
from app.models import Document
from app.models.document import BUSINESS_KEY_RELEASED
from app.pipeline.verify.issues import Issue, Severity
from app.pipeline.verify.normalize import (
    is_valid_trn,
    normalize_invoice_number,
    normalize_trn,
    to_ascii_digits,
)

CENT = Decimal("0.01")
_KEY_MAX = 255  # documents.vendor_key / business_key column size

# Legal forms that are not part of a supplier's identity ("Al Waha LLC" = "AL WAHA L.L.C.").
_LEGAL_SUFFIXES = frozenset(
    {
        "llc",
        "fze",
        "fzco",
        "fzllc",
        "fzc",
        "ltd",
        "limited",
        "est",
        "establishment",
        "inc",
        "co",
        "company",
        "plc",
        "pjsc",
        "psc",
        "wll",
        "llp",
        "sole",
        "proprietorship",
    }
)
_SPELLED_LLC = re.compile(r"\bl\s*\.?\s*l\s*\.?\s*c\b\.?", re.IGNORECASE)
_SPELLED_FZ_LLC = re.compile(r"\bf\s*\.?\s*z\s*[-.\s]*llc\b", re.IGNORECASE)


def normalize_vendor_name(name: str | None) -> str | None:
    """Lowercase, punctuation removed, trailing legal forms dropped, spaces collapsed."""
    if not name:
        return None
    text = to_ascii_digits(name).casefold().replace("&", " and ")
    text = _SPELLED_FZ_LLC.sub(" fzllc ", _SPELLED_LLC.sub(" llc ", text))
    words = re.sub(r"[^\w\s]", " ", text).split()
    # "Bright Clean & Co. LLC": drop LLC, Co and the "and" they leave dangling.
    while words and (words[-1] in _LEGAL_SUFFIXES or words[-1] == "and"):
        words.pop()
    return " ".join(words) or None


def vendor_key(invoice: Invoice) -> str | None:
    """The supplier's identity: its TRN when one is printed (spelling-proof), else its name."""
    if invoice.vendor_trn:
        digits = normalize_trn(invoice.vendor_trn)
        if is_valid_trn(digits):
            return f"TRN:{digits}"
    name = normalize_vendor_name(invoice.vendor_name)
    return f"NAME:{name}"[:_KEY_MAX] if name else None


def business_key(invoice: Invoice) -> str | None:
    """The document's identity for this supplier and type; None if it has neither a number
    nor a date + total."""
    if invoice.invoice_number:
        number = normalize_invoice_number(invoice.invoice_number)
        if number:
            return f"N:{number}"[:_KEY_MAX]
    if invoice.invoice_date is not None and invoice.total is not None:
        return f"F:{invoice.invoice_date.isoformat()}|{abs(invoice.total).quantize(CENT)}"
    return None


@dataclass(frozen=True, slots=True)
class DuplicateCheck:
    issues: list[Issue]
    vendor_key: str | None
    business_key: str | None  # None when a live duplicate already holds it (not claimable)


def check_duplicates(session: Session, document: Document, invoice: Invoice) -> DuplicateCheck:
    supplier, key = vendor_key(invoice), business_key(invoice)
    issues: list[Issue] = []
    if supplier and key and invoice.document_type is not DocumentType.NON_POSTABLE:
        other = _live_document(session, document, invoice.document_type, supplier, key)
        if other is not None:
            issues.append(_duplicate_issue(other))
            key = None
    if invoice.document_type is DocumentType.CREDIT_NOTE:
        issues += _credit_note_original(session, document, invoice, supplier)
    return DuplicateCheck(issues, supplier, key)


def claim_keys(session: Session, document: Document, check: DuplicateCheck) -> bool:
    """Store the keys on the document. False if another document claimed the same business
    key in the meantime (two copies verified at once): the unique index decides."""
    document.vendor_key = check.vendor_key
    document.business_key = check.business_key
    try:
        with session.begin_nested():
            session.flush()
    except IntegrityError:
        document.vendor_key = check.vendor_key  # the savepoint rollback expired both
        document.business_key = None
        session.flush()
        return False
    return True


def _live_document(
    session: Session, document: Document, kind: DocumentType, supplier: str, key: str
) -> Document | None:
    return session.scalars(
        select(Document)
        .where(
            Document.client_id == document.client_id,
            Document.id != document.id,
            Document.document_type == kind,
            Document.vendor_key == supplier,
            Document.business_key == key,
            Document.status.not_in(BUSINESS_KEY_RELEASED),
        )
        .order_by(Document.created_at)
        .limit(1)
    ).first()


def _credit_note_original(
    session: Session, document: Document, invoice: Invoice, supplier: str | None
) -> list[Issue]:
    reference = invoice.original_invoice_ref
    number = normalize_invoice_number(reference) if reference else None
    if not number or not supplier:
        return []  # missing reference is reported by the validation rules
    original = _live_document(session, document, DocumentType.BILL, supplier, f"N:{number}")
    if original is None:
        return [
            Issue(
                "credit_note_original_not_found",
                Severity.WARNING,
                "original_invoice_ref",
                f"The original invoice {reference} was not found in our records (it may exist "
                "in the accounting system).",
            )
        ]
    original_total = (original.invoice or {}).get("total")
    if invoice.total is None or original_total is None:
        return []
    if abs(invoice.total) > abs(Decimal(str(original_total))) + CENT:
        return [
            Issue(
                "credit_exceeds_original",
                Severity.ERROR,
                "total",
                f"The credit {abs(invoice.total)} is more than the original invoice "
                f"{reference} ({original_total}).",
            )
        ]
    return []


def _duplicate_issue(other: Document) -> Issue:
    name = PurePosixPath(other.source_path).name
    return Issue(
        "duplicate",
        Severity.ERROR,
        "invoice_number",
        f"Looks like a duplicate of document {other.id} ('{name}', {other.status.value}): "
        "same supplier, type and number. Reject it as a duplicate, or correct the number.",
    )
