"""Review (task 4.2): the accountant's queue, edits and rejections.

- Queue: documents in review, Exception first, then Check, then Ready; oldest first inside each.
- Edit: the accountant sends the whole corrected invoice. Fields that differ from the working
  copy, plus fields they confirmed unchanged, become human-verified (decision 24); then every
  deterministic check runs again, without the model. Setting the type to non-postable parks
  the document; giving a parked document a postable type brings it back to review.
- Reject: a reason from a fixed list, so rejections can be counted and learned from.

Every save also brings the document's corrections up to date (task 4.7). Approve comes with
the outbox in task 4.8. No login yet
(decision 26): changes are recorded as made by the Workbench.
"""

import json
import uuid
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Any, Final

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.adapters.storage import Storage
from app.core.config import Settings
from app.domain.enums import DocumentStatus, DocumentType, QueueCategory
from app.domain.invoice import Invoice
from app.domain.state_machine import transition
from app.models import Document
from app.pipeline.verify.issues import Severity
from app.pipeline.verify.step import Assessment, recheck_document, working_copy
from app.services.corrections import sync_corrections

ACTOR: Final = "workbench"  # until login (decision 26)
EDITABLE: Final = frozenset({DocumentStatus.IN_REVIEW, DocumentStatus.NON_POSTABLE})
# What the accountant may confirm: every invoice field except the model's own evidence.
CONFIRMABLE: Final = frozenset(Invoice.model_fields) - {"evidence"}

_QUEUE_ORDER = case(
    {QueueCategory.EXCEPTION: 0, QueueCategory.CHECK: 1, QueueCategory.READY: 2},
    value=Document.queue,
    else_=3,
)


class RejectReason(StrEnum):
    DUPLICATE = "DUPLICATE"  # the same document is already in (or was already posted)
    ALREADY_RECORDED = "ALREADY_RECORDED"  # entered in the accounting system by hand
    WRONG_RESTAURANT = "WRONG_RESTAURANT"  # belongs to another client
    UNREADABLE = "UNREADABLE"  # too blurred or incomplete to enter
    OTHER = "OTHER"  # needs a note


class ReviewError(Exception):
    """The request cannot be applied; the message is safe to show the accountant."""


class NotEditableError(ReviewError):
    def __init__(self, status: DocumentStatus) -> None:
        super().__init__(f"a document in status {status.value} cannot be changed in review")
        self.status = status


@dataclass(frozen=True, slots=True)
class QueuePage:
    documents: list[Document]
    total: int


def list_queue(
    session: Session,
    *,
    client_id: str,
    status: DocumentStatus = DocumentStatus.IN_REVIEW,
    queue: QueueCategory | None = None,
    run_id: uuid.UUID | None = None,
    limit: int = 50,
    offset: int = 0,
) -> QueuePage:
    query = select(Document).where(Document.client_id == client_id, Document.status == status)
    if queue is not None:
        query = query.where(Document.queue == queue)
    if run_id is not None:
        query = query.where(Document.run_id == run_id)
    total = session.scalar(select(func.count()).select_from(query.subquery())) or 0
    documents = session.scalars(
        query.order_by(_QUEUE_ORDER, Document.created_at, Document.id).limit(limit).offset(offset)
    ).all()
    return QueuePage(list(documents), total)


def get_document(
    session: Session, document_id: uuid.UUID, *, for_update: bool = False
) -> Document | None:
    """`for_update` locks the row until the transaction ends, so two edits cannot interleave."""
    return session.get(Document, document_id, with_for_update=for_update)


def update_document(
    session: Session,
    storage: Storage,
    document: Document,
    invoice: Invoice,
    *,
    confirmed: Collection[str] = (),
    settings: Settings,
    today: date,
) -> Assessment:
    """Save the accountant's version of the invoice and check it again. Runs in the caller's
    transaction; the caller commits."""
    if document.status not in EDITABLE:
        raise NotEditableError(document.status)
    unknown = sorted(set(confirmed) - CONFIRMABLE)
    if unknown:
        raise ReviewError(f"unknown field(s) to confirm: {', '.join(unknown)}")

    before = working_copy(document)
    # The evidence is what the model quoted from the page: kept, never edited.
    invoice = invoice.model_copy(update={"evidence": before.evidence if before else {}})
    verified = set(document.verified_fields or ()) | changed_fields(before, invoice)
    document.verified_fields = sorted(verified | set(confirmed))
    document.invoice = json.loads(invoice.model_dump_json())

    parked = invoice.document_type is DocumentType.NON_POSTABLE
    if document.status is DocumentStatus.IN_REVIEW and parked:
        transition(session, document, DocumentStatus.NON_POSTABLE, actor=ACTOR,
                   reason="marked non-postable in review")  # fmt: skip
    elif document.status is DocumentStatus.NON_POSTABLE and not parked:
        transition(session, document, DocumentStatus.IN_REVIEW, actor=ACTOR,
                   reason=f"type changed to {invoice.document_type.value} in review")  # fmt: skip
    assessment = recheck_document(session, storage, document, settings=settings, today=today)
    sync_corrections(session, document, corrected_by=ACTOR)  # after the re-check: current supplier
    return assessment


def reject_document(
    session: Session, document: Document, reason: RejectReason, *, note: str | None = None
) -> None:
    """Rejected documents release their business key (decision 18), so a corrected copy can
    come in later."""
    note = (note or "").strip() or None
    if reason is RejectReason.OTHER and note is None:
        raise ReviewError("a rejection with reason OTHER needs a note")
    if document.status is not DocumentStatus.IN_REVIEW:
        raise NotEditableError(document.status)
    details: dict[str, Any] = {"note": note} if note else {}
    transition(session, document, DocumentStatus.REJECTED, actor=ACTOR, reason=reason.value,
               details=details)  # fmt: skip


def changed_fields(before: Invoice | None, after: Invoice) -> set[str]:
    """Top-level invoice fields whose value differs (Decimals compare by value: 1050 equals
    1050.00). With no earlier copy (the extraction failed), every field that was filled in."""
    fields: Sequence[str] = [name for name in Invoice.model_fields if name != "evidence"]
    if before is None:
        return {name for name in fields if getattr(after, name) not in (None, [])}
    return {name for name in fields if getattr(before, name) != getattr(after, name)}


def issue_counts(document: Document) -> tuple[int, int]:
    """(errors, warnings) of the last check."""
    issues = document.issues or []
    errors = sum(issue.get("severity") == Severity.ERROR for issue in issues)
    return errors, len(issues) - errors
