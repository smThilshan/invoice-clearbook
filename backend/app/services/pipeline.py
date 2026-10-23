"""Pipeline control: what runs next, failing, retrying and resuming documents.

Flow: CAPTURED -> read -> READ -> extract -> EXTRACTED -> verify -> IN_REVIEW (or parked).
Each step queues the next only after its own transaction commits. A step that fails
unexpectedly marks the document FAILED with the reason; FAILED documents can be retried from
the start. Documents left between steps (a worker crashed after committing, before queuing
the next step) are found by `stalled_documents` and resumed.
"""

from datetime import datetime, timedelta
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.enums import DocumentStatus
from app.domain.state_machine import can_transition, transition
from app.models import Document, Run, RunStatus


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
