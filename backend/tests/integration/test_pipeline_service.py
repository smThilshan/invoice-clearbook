from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.domain.enums import DocumentStatus as S
from app.domain.state_machine import InvalidTransitionError
from app.models import AuditLog, Document
from app.services.pipeline import fail_document, retry_document, stalled_documents
from tests.integration.factories import make_document

pytestmark = pytest.mark.integration


def last_audit(session: Session, doc: Document) -> AuditLog:
    return session.scalars(
        select(AuditLog).where(AuditLog.document_id == doc.id).order_by(AuditLog.id.desc())
    ).first() or pytest.fail("no audit entry")


def test_fail_document_records_step_and_reason(session: Session) -> None:
    doc = make_document(session, status=S.READ)

    assert fail_document(session, doc, step="extract", reason="model unavailable")

    assert doc.status is S.FAILED
    assert doc.failure_reason == "extract: model unavailable"
    entry = last_audit(session, doc)
    assert (entry.from_status, entry.actor) == (S.READ, "system:extract")


def test_fail_document_leaves_documents_that_moved_on(session: Session) -> None:
    doc = make_document(session, status=S.IN_REVIEW)

    assert not fail_document(session, doc, step="read", reason="late crash")
    assert doc.status is S.IN_REVIEW


def test_retry_sends_failed_document_back_to_start(session: Session) -> None:
    doc = make_document(session, status=S.CAPTURED)
    fail_document(session, doc, step="read", reason="cannot open PDF")

    retry_document(session, doc, actor="admin:cli")

    assert (doc.status, doc.failure_reason) == (S.CAPTURED, None)
    entry = last_audit(session, doc)
    assert entry.details == {"previous_failure": "read: cannot open PDF"}


def test_retry_refuses_documents_that_did_not_fail(session: Session) -> None:
    with pytest.raises(InvalidTransitionError):
        retry_document(session, make_document(session, status=S.READ), actor="admin:cli")


def test_stalled_documents_are_old_and_waiting_for_a_step(session: Session) -> None:
    old_captured = make_document(session, status=S.CAPTURED)
    old_read = make_document(session, status=S.READ)
    old_extracted = make_document(session, status=S.EXTRACTED)  # waits for Phase 3, not stalled
    make_document(session, status=S.CAPTURED)  # recent: may still be running
    long_ago = datetime.now(UTC) - timedelta(hours=2)
    session.execute(
        update(Document)
        .where(Document.id.in_([old_captured.id, old_read.id, old_extracted.id]))
        .values(updated_at=long_ago)
        .execution_options(synchronize_session=False)
    )

    stalled = stalled_documents(session, idle_for=timedelta(hours=1), now=datetime.now(UTC))

    assert {doc.id for doc in stalled} == {old_captured.id, old_read.id}
