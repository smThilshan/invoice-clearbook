"""Supplier match (task 3.5, architecture §8): link a document to a known contact.

TRN first (exact, spelling-proof), then a similar name (pg_trgm). Only the client's active
contacts of its accounting provider are candidates. Outcomes:
- matched by TRN or by name (similarity >= match threshold, or same normalized name)
- a possible match to confirm (similarity >= suggest threshold): warning
- a new supplier, not in master data: warning ("warning if new")
A name match whose printed TRN differs from the one on file is warned: wrong supplier, or
changed details (fraud alerts proper come later, Phase 7).
"""

import uuid
from dataclasses import dataclass, field
from enum import StrEnum

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.domain.enums import AccountingProvider, DocumentType
from app.domain.invoice import Invoice
from app.models import Contact
from app.pipeline.verify.duplicates import normalize_vendor_name
from app.pipeline.verify.issues import Issue, Severity
from app.pipeline.verify.normalize import is_valid_trn, normalize_trn

_CANDIDATES = 5


class MatchMethod(StrEnum):
    TRN = "TRN"
    NAME = "NAME"
    NONE = "NONE"


@dataclass(frozen=True, slots=True)
class VendorMatch:
    contact_id: uuid.UUID | None
    method: MatchMethod
    similarity: float | None = None  # name matches only
    issues: list[Issue] = field(default_factory=list)


def match_vendor(
    session: Session,
    invoice: Invoice,
    *,
    client_id: str,
    provider: AccountingProvider,
    min_similarity: float,
    suggest_similarity: float,
) -> VendorMatch:
    if invoice.document_type is DocumentType.NON_POSTABLE:
        return VendorMatch(None, MatchMethod.NONE)
    contacts = (
        select(Contact)
        .where(
            Contact.client_id == client_id,
            Contact.provider == provider,
            Contact.is_active.is_(True),
        )
        .order_by(Contact.name)
    )
    printed_trn = normalize_trn(invoice.vendor_trn) if invoice.vendor_trn else ""
    if is_valid_trn(printed_trn):
        # Master data keeps the TRN as typed ("100-9990-..."): compare digits only.
        by_trn = session.scalars(
            contacts.where(func.regexp_replace(Contact.tax_number, r"\D", "", "g") == printed_trn)
        ).first()
        if by_trn is not None:
            return VendorMatch(by_trn.id, MatchMethod.TRN)

    if not invoice.vendor_name:
        return VendorMatch(None, MatchMethod.NONE)  # a missing name is a validation issue
    contact, similarity = _closest_by_name(session, contacts, invoice.vendor_name)

    if contact is not None and similarity >= min_similarity:
        issues = []
        on_file = normalize_trn(contact.tax_number) if contact.tax_number else ""
        if is_valid_trn(printed_trn) and is_valid_trn(on_file) and printed_trn != on_file:
            issues.append(
                Issue(
                    "vendor_trn_differs",
                    Severity.WARNING,
                    "vendor_trn",
                    f"The supplier matches '{contact.name}' by name, but the TRN on the document "
                    f"({printed_trn}) differs from the one on file ({on_file}): wrong supplier, "
                    "or changed details? Check before approving.",
                )
            )
        return VendorMatch(contact.id, MatchMethod.NAME, similarity, issues)

    if contact is not None and similarity >= suggest_similarity:
        return VendorMatch(
            None,
            MatchMethod.NONE,
            similarity,
            [
                Issue(
                    "vendor_possible_match",
                    Severity.WARNING,
                    "vendor_name",
                    f"Possible match: '{contact.name}' (similarity {similarity:.2f}). "
                    "Confirm it, or treat this as a new supplier.",
                )
            ],
        )
    return VendorMatch(
        None,
        MatchMethod.NONE,
        None,
        [
            Issue(
                "vendor_new",
                Severity.WARNING,
                "vendor_name",
                f"New supplier: '{invoice.vendor_name}' is not in the master data.",
            )
        ],
    )


def _closest_by_name(
    session: Session, contacts: Select[Contact], name: str
) -> tuple[Contact | None, float]:
    """Best candidate by trigram similarity; an identical normalized name counts as 1.0.
    `%` (pg_trgm, threshold 0.3) uses the GIN index on contacts.name, so this scales."""
    similarity = func.similarity(Contact.name, name)
    rows = session.execute(
        contacts.add_columns(similarity)
        .where(Contact.name.op("%")(name))
        .order_by(similarity.desc(), Contact.name)
        .limit(_CANDIDATES)
    ).all()
    wanted = normalize_vendor_name(name)
    for contact, _ in rows:
        if wanted and normalize_vendor_name(contact.name) == wanted:
            return contact, 1.0
    if not rows:
        return None, 0.0
    best, score = rows[0]
    return best, float(score)
