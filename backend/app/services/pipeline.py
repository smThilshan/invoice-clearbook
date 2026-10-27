"""Pipeline control: what runs next, failing, retrying and resuming documents.

Flow: CAPTURED -> read -> READ -> extract -> EXTRACTED -> verify -> IN_REVIEW (or parked).
Each step queues the next only after its own transaction commits. A step that fails
unexpectedly marks the document FAILED with the reason; FAILED documents can be retried from
the start. A document waiting in review can be extracted again (decision 55). Documents left
between steps (a worker crashed after committing, before queuing
the next step) are found by `stalled_documents` and resumed.
"""

from datetime import datetime, timedelta
from enum import StrEnum

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.domain.enums import DocumentStatus
from app.domain.state_machine import can_transition, transition
from app.models import Correction, Document, Run, RunStatus


class Step(StrEnum):
    READ = "read"
    EXTRACT = "extract"
    VERIFY = "verify"


# Status a document must be in for each step to do work.
NEXT_STEP: dict[DocumentStatus, Step] = {
    DocumentStatus.CAPTURED: Step.READ,
    DocumentStatus.READ: Step.EXTRACT,
    DocumentStatus.EXTRACTED: Step.VERIFY,
}

_REASON_MAX = 500


def fail_document(session: Session, document: Document, *, step: str, reason: str) -> bool:
    """Mark FAILED with the reason. False if the document is no longer in a failable state
    (e.g. another worker already moved it on)."""
    if not can_transition(document.status, DocumentStatus.FAILED):
        return False
    reason = f"{step}: {reason}"[:_REASON_MAX]
    document.failure_reason = reason
    transition(session, document, DocumentStatus.FAILED, actor=f"system:{step}", reason=reason)
    return True


def retry_document(session: Session, document: Document, *, actor: str) -> None:
    """Send a FAILED document back to the start. Raises InvalidTransitionError otherwise."""
    previous = document.failure_reason
    transition(
        session,
        document,
        DocumentStatus.CAPTURED,
        actor=actor,
        reason="retry",
        details={"previous_failure": previous},
    )
    document.failure_reason = None


def reextract_document(session: Session, document: Document, *, actor: str) -> None:
    """Back to the model (decision 55), for after an app or model update: the pages are kept
    (not read again), the model's earlier answers stay in `extractions`. What review did on the
    current version is replaced: verified fields, codes and correction rows. Only documents
    waiting in review or parked; raises InvalidTransitionError otherwise."""
    transition(session, document, DocumentStatus.READ, actor=actor, reason="extract again")
    document.verified_fields = []
    document.coding = None
    document.issues = None
    document.queue = None
    session.execute(delete(Correction).where(Correction.document_id == document.id))


def stalled_documents(session: Session, *, idle_for: timedelta, now: datetime) -> list[Document]:
    """Documents waiting for a step that nobody is running: untouched for longer than any
    job may take (`idle_for` = the job time limit), so they cannot still be in progress."""
    return list(
        session.scalars(
            select(Document)
            .join(Run, Run.id == Document.run_id)
            .where(
                Document.status.in_(tuple(NEXT_STEP)),
                Document.updated_at < now - idle_for,
                Run.status != RunStatus.STOPPED,  # stopped by the user: only Resume continues it
            )
            .order_by(Document.created_at)
        )
    )
