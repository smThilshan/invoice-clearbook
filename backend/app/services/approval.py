"""Approve (task 4.8): the last step of review, the first of posting.

    re-check (no model) -> any error: refused, nothing changes
    -> IN_REVIEW -> APPROVED  +  outbox row (PENDING)  +  supplier mapping  +  corrections
       all in the caller's one transaction (rule 11)

Only the Celery posting worker (Phase 5) reads the outbox and calls the accounting system; the
database lets only APPROVED documents move to POSTING (decision 19). Warnings do not block: the
accountant has seen them.

Supplier mapping (architecture §9, source 1): a document whose lines all use one account (and one
VAT type) teaches that the supplier is coded so. The same choice again counts one more consistent
approval; a different one starts over at one. Coding trusts a mapping after
`coding_min_consistent_approvals` (default 3). Documents with mixed accounts teach nothing, and a
document approved again (sent back, or deleted there and sent again) does not count twice.
"""

from datetime import UTC, date, datetime

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from app.adapters.storage import Storage
from app.core.config import Settings
from app.domain.enums import DocumentStatus, LineGrouping, TaxType
from app.domain.invoice import Invoice
from app.domain.state_machine import transition
from app.models import Client, Document, Outbox, VendorMapping
from app.pipeline.payment import PURCHASES
from app.pipeline.verify.step import recheck_document, working_copy
from app.services.corrections import sync_corrections
from app.services.review import NotEditableError, ReviewError


class ApprovalBlockedError(ReviewError):
    """The re-check found errors: approve after fixing them."""

    def __init__(self, errors: int) -> None:
        super().__init__(
            f"{errors} error{'s' if errors != 1 else ''} must be fixed before approval"
        )
        self.errors = errors


def approve_document(
    session: Session,
    storage: Storage,
    document: Document,
    *,
    settings: Settings,
    today: date,
    actor: str,
) -> Outbox:
    """Runs in the caller's transaction; the caller commits (or rolls back on an error).
    `actor`: who approves (the user's email)."""
    if document.status is not DocumentStatus.IN_REVIEW:
        raise NotEditableError(document.status)
    assessment = recheck_document(session, storage, document, settings=settings, today=today)
    invoice = working_copy(document)
    if assessment.errors or invoice is None:
        raise ApprovalBlockedError(assessment.errors or 1)

    sync_corrections(session, document, corrected_by=actor)
    summary = {"queue": assessment.queue.value, "warnings": assessment.warnings}
    transition(session, document, DocumentStatus.APPROVED, actor=actor, details=summary)
    document.failure_reason = None  # an earlier "not sent" reason is now answered
    grouping = (
        session.scalar(select(Client.line_grouping).where(Client.client_id == document.client_id))
        or LineGrouping.BY_ITEM
    )
    approved_before = session.scalar(
        select(exists().where(Outbox.document_id == document.id))
    )  # sent back, or deleted in the accounting system and sent again (decision 76)
    outbox = Outbox(client_id=document.client_id, document_id=document.id, line_grouping=grouping)
    session.add(outbox)
    if not approved_before:  # one document is one approval for the supplier's coding
        _learn_supplier_coding(session, document, invoice)
        trusted_after = settings.coding_min_consistent_approvals
        _learn_supplier_payment(session, document, invoice, trusted_after)
    session.flush()
    return outbox


def _learn_supplier_coding(session: Session, document: Document, invoice: Invoice) -> None:
    accounts = {line.account_code for line in invoice.lines}
    tax_types = {line.tax_type for line in invoice.lines}
    if document.vendor_key is None or len(accounts) != 1 or len(tax_types) != 1:
        return
    (account,), (tax_type,) = accounts, tax_types
    if account is None:
        return
    mapping = _mapping(session, document)
    if _same_choice(mapping, account, tax_type):
        mapping.consistent_approvals += 1
    else:
        mapping.account_code, mapping.tax_type, mapping.consistent_approvals = account, tax_type, 1
    mapping.last_approved_at = datetime.now(UTC)


def _learn_supplier_payment(
    session: Session, document: Document, invoice: Invoice, trusted_after: int
) -> None:
    """Bill or expense for this supplier (decision 88). The accountant's own choice counts at
    once; the type chosen by the checks after two approvals in a row, like its coding."""
    kind = invoice.document_type
    if document.vendor_key is None or kind not in PURCHASES:
        return
    mapping = _mapping(session, document)
    chosen = "document_type" in (document.verified_fields or ())
    if mapping.payment_type is kind and mapping.payment_approvals > 0:
        mapping.payment_approvals += 1
    else:
        mapping.payment_type, mapping.payment_approvals = kind, 1
    if chosen:
        mapping.payment_approvals = max(mapping.payment_approvals, trusted_after)
    mapping.last_approved_at = datetime.now(UTC)


def _mapping(session: Session, document: Document) -> VendorMapping:
    mapping = session.scalars(
        select(VendorMapping).where(
            VendorMapping.client_id == document.client_id,
            VendorMapping.vendor_key == document.vendor_key,
        )
    ).first()
    if mapping is None:
        # The column defaults only apply on insert: count from 0 here, before the first save.
        mapping = VendorMapping(
            client_id=document.client_id,
            vendor_key=document.vendor_key,
            consistent_approvals=0,
            payment_approvals=0,
        )
        session.add(mapping)
    return mapping


def _same_choice(mapping: VendorMapping, account: str, tax_type: TaxType | None) -> bool:
    return (
        mapping.consistent_approvals > 0
        and mapping.account_code == account
        and mapping.tax_type == tax_type
    )
