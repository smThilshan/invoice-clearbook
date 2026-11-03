"""Review (task 4.2): the accountant's queue, edits and rejections.

- Queue: documents in review, Exception first, then Check, then Ready; oldest first inside each.
- Edit: the accountant sends the whole corrected invoice. Fields that differ from the working
  copy, plus fields they confirmed unchanged, become human-verified (decision 24); then every
  deterministic check runs again, without the model. Setting the type to non-postable parks
  the document; giving a parked document a postable type brings it back to review. Changing
  the type from non-postable to a postable one also codes the lines nobody coded yet (verify
  skipped them): the one edit that may ask the model.
- Reject: a reason from a fixed list, so rejections can be counted and learned from.

Every save also brings the document's corrections up to date (task 4.7). Approve comes with
the outbox in task 4.8. No login yet
(decision 26): changes were recorded as made by the Workbench. Since login (decision 71) every
change names the user who made it (`actor`, their email).
"""

import json
import uuid
from collections.abc import Callable, Collection, Sequence
from contextlib import closing
from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from typing import Any, Final

from sqlalchemy import and_, case, exists, func, select
from sqlalchemy.orm import Session

from app.adapters.llm import ExtractionClient
from app.adapters.storage import Storage
from app.core.config import Settings
from app.domain.enums import DocumentStatus, DocumentType, QueueCategory
from app.domain.invoice import Invoice
from app.domain.state_machine import transition
from app.models import Document, Outbox, OutboxStatus, Run, RunStatus
from app.pipeline.verify.issues import Severity
from app.pipeline.verify.step import (
    Assessment,
    code_empty_lines,
    recheck_document,
    working_copy,
)
from app.services.corrections import sync_corrections
from app.services.posting import POSTED_STATUSES, SENDABLE

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


class SendingList(StrEnum):
    """The queue's lists of approved documents (decision 70)."""

    WAITING = "waiting"  # on its way: waiting, sending, retrying or paused
    FAILED = "failed"  # gave up: Try again or Send back
    POSTED = "posted"  # in the accounting system


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
    # Task 4.16: documents per category for the same filters (every category, whatever is
    # selected), and the client's documents still being processed (not in the list yet).
    counts: dict[QueueCategory, int]
    processing: int
    sending_counts: dict["SendingList", int] = field(default_factory=dict)  # same filters
    # Not finished because their run was stopped (task 4.19): waiting for Resume, not processing.
    stopped: int = 0
    stopped_run_id: uuid.UUID | None = None  # the latest such run, to open and resume


def list_queue(
    session: Session,
    *,
    client_id: str,
    status: DocumentStatus = DocumentStatus.IN_REVIEW,
    queue: QueueCategory | None = None,
    run_id: uuid.UUID | None = None,
    search: str | None = None,
    sending: SendingList | None = None,
    limit: int = 50,
    offset: int = 0,
) -> QueuePage:
    """`sending`: one of the lists of approved documents instead of `status` (decision 70)."""
    scope = select(Document).where(Document.client_id == client_id)
    if run_id is not None:
        scope = scope.where(Document.run_id == run_id)
    if search and search.strip():
        scope = scope.where(_matches(search.strip()))
    sending_counts = {
        name: session.scalar(scope.where(_sending(name)).with_only_columns(func.count())) or 0
        for name in SendingList
    }
    counts: dict[QueueCategory, int] = {}
    if sending is not None:
        query = scope.where(_sending(sending))
        newest_first = sending is not SendingList.WAITING  # what just happened, on top
        order: tuple[Any, ...] = (
            Document.updated_at.desc() if newest_first else Document.updated_at,
            Document.id,
        )
    else:
        base = scope.where(Document.status == status)
        counts = {
            category: count
            for category, count in session.execute(
                base.with_only_columns(Document.queue, func.count()).group_by(Document.queue)
            )
            if category is not None
        }
        query = base.where(Document.queue == queue) if queue is not None else base
        order = (_QUEUE_ORDER, Document.created_at, Document.id)
    total = session.scalar(select(func.count()).select_from(query.subquery())) or 0
    documents = session.scalars(query.order_by(*order).limit(limit).offset(offset)).all()
    unfinished = (
        select(Run.id, Run.status, Run.created_at, func.count().label("documents"))
        .join(Document, Document.run_id == Run.id)
        .where(Document.client_id == client_id, Document.status.in_(PROCESSING))
        .group_by(Run.id)
        .order_by(Run.created_at.desc())
    )
    if run_id is not None:
        unfinished = unfinished.where(Document.run_id == run_id)
    runs = session.execute(unfinished).all()
    stopped = [row for row in runs if row.status is RunStatus.STOPPED]
    return QueuePage(
        list(documents),
        total,
        counts,
        processing=sum(row.documents for row in runs if row.status is not RunStatus.STOPPED),
        sending_counts=sending_counts,
        stopped=sum(row.documents for row in stopped),
        stopped_run_id=stopped[0].id if stopped else None,
    )


def _sending(name: SendingList) -> Any:
    gave_up = exists().where(Outbox.document_id == Document.id, Outbox.status == OutboxStatus.DEAD)
    match name:
        case SendingList.WAITING:
            return and_(Document.status.in_(SENDABLE), ~gave_up)
        case SendingList.FAILED:
            return and_(Document.status.in_(SENDABLE), gave_up)
        case SendingList.POSTED:
            return Document.status.in_(POSTED_STATUSES)


# Statuses whose pipeline step is still to come or running: not in the review queue yet.
PROCESSING = (
    DocumentStatus.CAPTURED,
    DocumentStatus.READ,
    DocumentStatus.EXTRACTED,
    DocumentStatus.VERIFIED,
)


def _matches(search: str) -> Any:
    """Supplier name, invoice number or file name containing the text (any case)."""
    escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    pattern = f"%{escaped}%"
    return (
        Document.invoice["vendor_name"].astext.ilike(pattern, escape="\\")
        | Document.invoice["invoice_number"].astext.ilike(pattern, escape="\\")
        | Document.source_path.ilike(pattern, escape="\\")
    )


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
    actor: str,
    llm: Callable[[], ExtractionClient] | None = None,
) -> Assessment:
    """Save the accountant's version of the invoice and check it again. Runs in the caller's
    transaction; the caller commits. `llm` makes the model client, only if coding needs it;
    `actor`: who made the change (the user's email)."""
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
    if llm is not None and _made_postable(before, invoice):
        with closing(llm()) as client:
            invoice = code_empty_lines(session, document, invoice, client, settings=settings)
    document.invoice = json.loads(invoice.model_dump_json())

    parked = invoice.document_type is DocumentType.NON_POSTABLE
    if document.status is DocumentStatus.IN_REVIEW and parked:
        transition(session, document, DocumentStatus.NON_POSTABLE, actor=actor,
                   reason="marked non-postable in review")  # fmt: skip
    elif document.status is DocumentStatus.NON_POSTABLE and not parked:
        transition(session, document, DocumentStatus.IN_REVIEW, actor=actor,
                   reason=f"type changed to {invoice.document_type.value} in review")  # fmt: skip
    assessment = recheck_document(session, storage, document, settings=settings, today=today)
    sync_corrections(session, document, corrected_by=actor)  # after the re-check: current supplier
    return assessment


def _made_postable(before: Invoice | None, after: Invoice) -> bool:
    return (
        before is not None
        and before.document_type is DocumentType.NON_POSTABLE
        and after.document_type is not DocumentType.NON_POSTABLE
    )


def reject_document(
    session: Session,
    document: Document,
    reason: RejectReason,
    *,
    actor: str,
    note: str | None = None,
) -> None:
    """Rejected documents release their business key (decision 18), so a corrected copy can
    come in later."""
    note = (note or "").strip() or None
    if reason is RejectReason.OTHER and note is None:
        raise ReviewError("a rejection with reason OTHER needs a note")
    if document.status is not DocumentStatus.IN_REVIEW:
        raise NotEditableError(document.status)
    details: dict[str, Any] = {"note": note} if note else {}
    transition(session, document, DocumentStatus.REJECTED, actor=actor, reason=reason.value,
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
