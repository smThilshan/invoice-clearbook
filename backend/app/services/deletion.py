"""Rejected documents (decision 45): bring one back to review, or delete it permanently.

Reject is the everyday way out of the queue and keeps the record. Deleting is the second step,
from the Rejected list, for files that must not stay (another business's invoice, a wrong or
test file): the document, its AI answers, corrections, history, send attempts and stored files
are removed, and one `deletions` row is kept as proof (no invoice content). A document that
reached the accounting system (it has an external ID) is never deleted here: void it there.

The caller commits, then removes the files: a failed file removal leaves a stray file behind,
never a record pointing to a file that is gone.
"""

from datetime import date

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.adapters.storage import Storage
from app.adapters.storage.keys import charge_ocr_key, ocr_key, page_image_key
from app.core.config import Settings
from app.domain.enums import DocumentStatus
from app.domain.state_machine import transition
from app.models import AuditLog, Deletion, Document, Outbox
from app.pipeline.verify.step import recheck_document
from app.services.review import ACTOR, ReviewError


class NotRejectedError(ReviewError):
    def __init__(self, status: DocumentStatus) -> None:
        super().__init__(f"only a rejected document can be brought back or deleted ({status})")


def bring_back(
    session: Session, storage: Storage, document: Document, *, settings: Settings, today: date
) -> None:
    """Rejected -> in review, checked again. Refused when another live document now holds its
    supplier and invoice number (decision 18). The caller commits."""
    if document.status is not DocumentStatus.REJECTED:
        raise NotRejectedError(document.status)
    try:
        with session.begin_nested():
            transition(session, document, DocumentStatus.IN_REVIEW, actor=ACTOR,
                       reason="brought back")  # fmt: skip
    except IntegrityError as exc:
        raise ReviewError(
            "another document with the same supplier and invoice number is in use: reject or "
            "delete that one first"
        ) from exc
    recheck_document(session, storage, document, settings=settings, today=today)


def delete_permanently(session: Session, document: Document) -> list[str]:
    """Remove the document and everything about it; the storage keys to remove after the
    caller commits."""
    if document.status is not DocumentStatus.REJECTED:
        raise NotRejectedError(document.status)
    if document.external_id:
        raise ReviewError("this document is in the accounting system: void it there instead")
    reason = session.scalars(
        select(AuditLog.reason)
        .where(AuditLog.document_id == document.id, AuditLog.to_status == DocumentStatus.REJECTED)
        .order_by(AuditLog.id.desc())
        .limit(1)
    ).first()
    keys = _files(session, document)

    session.execute(delete(Outbox).where(Outbox.document_id == document.id))
    session.execute(delete(AuditLog).where(AuditLog.document_id == document.id))
    session.add(
        Deletion(
            client_id=document.client_id,
            document_id=document.id,
            sha256=document.sha256,
            page_count=document.page_count,
            rejected_reason=reason,
            deleted_by=ACTOR,
        )
    )
    session.delete(document)  # extractions and corrections go with it (ON DELETE CASCADE)
    session.flush()
    return keys


def _files(session: Session, document: Document) -> list[str]:
    pages = range(1, (document.page_count or 0) + 1)
    keys = [
        key(document.client_id, document.id, page)
        for page in pages
        for key in (page_image_key, ocr_key, charge_ocr_key)
    ]
    shared = session.scalars(
        select(func.count()).where(
            Document.storage_key == document.storage_key, Document.id != document.id
        )
    ).one()
    if not shared:  # a duplicate's record still points to the same original: keep it
        keys.append(document.storage_key)
    return keys
