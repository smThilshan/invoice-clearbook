from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.domain.enums import DocumentStatus as S
from app.domain.enums import DocumentType
from app.domain.state_machine import InvalidTransitionError
from app.models import AuditLog, Correction, Document
from app.services.pipeline import (
    fail_document,
    reextract_document,
    retry_document,
    stalled_documents,
)
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


@pytest.mark.parametrize("status", [S.IN_REVIEW, S.NON_POSTABLE])
def test_reextract_sends_a_document_in_review_back_to_the_model(
    session: Session, status: S
) -> None:
    doc = make_document(session, status=status, verified_fields=["total"], queue=None,
                        coding=[{"line": 0}], issues=[{"code": "x"}])  # fmt: skip
    session.add(
        Correction(
            client_id=doc.client_id, document_id=doc.id, field="total", corrected_by="workbench"
        )
    )
    session.flush()

    reextract_document(session, doc, actor="admin:cli")

    assert doc.status is S.READ
    assert (doc.verified_fields, doc.coding, doc.issues, doc.queue) == ([], None, None, None)
    assert session.scalars(select(Correction).where(Correction.document_id == doc.id)).all() == []
    entry = last_audit(session, doc)
    assert (entry.from_status, entry.actor, entry.reason) == (status, "admin:cli", "extract again")


def test_reextract_lets_the_new_answer_change_the_type(session: Session) -> None:
    """Decision 61: a bill read again may come back a receipt. Its old key must not block the
    change when a receipt of that supplier holds the same number (the check claims it again)."""
    keys = {"vendor_key": "TRN:100304657800003", "business_key": "N:147"}
    make_document(session, status=S.IN_REVIEW, document_type=DocumentType.RECEIPT, **keys)
    bill = make_document(session, status=S.IN_REVIEW, document_type=DocumentType.BILL, **keys)

    reextract_document(session, bill, actor="admin:cli")
    bill.document_type = DocumentType.RECEIPT  # what extraction then sets
    session.flush()

    assert bill.business_key is None


@pytest.mark.parametrize("status", [S.APPROVED, S.POSTED, S.REJECTED, S.FAILED, S.EXTRACTED])
def test_reextract_refuses_documents_not_waiting_in_review(session: Session, status: S) -> None:
    with pytest.raises(InvalidTransitionError):
        reextract_document(session, make_document(session, status=status), actor="admin:cli")


def test_stalled_documents_are_old_and_waiting_for_a_step(session: Session) -> None:
    old_captured = make_document(session, status=S.CAPTURED)
    old_read = make_document(session, status=S.READ)
    old_extracted = make_document(session, status=S.EXTRACTED)  # waiting for verify
    old_in_review = make_document(session, status=S.IN_REVIEW)  # waits for a person, not stalled
    make_document(session, status=S.CAPTURED)  # recent: may still be running
    long_ago = datetime.now(UTC) - timedelta(hours=2)
    session.execute(
        update(Document)
        .where(Document.id.in_([old_captured.id, old_read.id, old_extracted.id, old_in_review.id]))
        .values(updated_at=long_ago)
        .execution_options(synchronize_session=False)
    )

    stalled = stalled_documents(session, idle_for=timedelta(hours=1), now=datetime.now(UTC))

    assert {doc.id for doc in stalled} == {old_captured.id, old_read.id, old_extracted.id}
