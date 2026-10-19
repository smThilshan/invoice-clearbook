import itertools

import pytest
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.domain.enums import DocumentStatus as S
from app.domain.state_machine import InvalidTransitionError, StaleStatusError, transition
from app.models import AuditLog, Document
from tests.integration.factories import make_document

pytestmark = pytest.mark.integration


def _audit(session: Session, document: Document) -> list[AuditLog]:
    return list(
        session.scalars(
            select(AuditLog).where(AuditLog.document_id == document.id).order_by(AuditLog.id)
        )
    )


def _db_status(session: Session, document: Document) -> S:
    return session.scalars(select(Document.status).where(Document.id == document.id)).one()


def test_transition_updates_status_and_writes_audit(session: Session) -> None:
    document = make_document(session)

    transition(session, document, S.READ, actor="system:read", reason="text layer found")
    session.flush()

    assert document.status is S.READ
    assert _db_status(session, document) is S.READ
    [entry] = _audit(session, document)
    assert (entry.from_status, entry.to_status) == (S.CAPTURED, S.READ)
    assert entry.actor == "system:read"
    assert entry.reason == "text layer found"
    assert entry.client_id == document.client_id


def test_invalid_transition_writes_nothing(session: Session) -> None:
    document = make_document(session)

    with pytest.raises(InvalidTransitionError):
        transition(session, document, S.APPROVED, actor="tester")
    session.flush()

    assert _db_status(session, document) is S.CAPTURED
    assert _audit(session, document) == []


def test_stale_status_refused(session: Session) -> None:
    document = make_document(session)
    # Another worker moves it first; our in-memory copy still says CAPTURED.
    session.execute(
        update(Document)
        .where(Document.id == document.id)
        .values(status=S.READ)
        .execution_options(synchronize_session=False)
    )

    with pytest.raises(StaleStatusError):
        transition(session, document, S.READ, actor="worker-2")
    session.flush()

    assert _audit(session, document) == []


def test_full_path_to_posted_passes_db_trigger(session: Session) -> None:
    path = [
        S.CAPTURED, S.READ, S.EXTRACTED, S.VERIFIED, S.IN_REVIEW,
        S.APPROVED, S.POSTING, S.POSTED,
    ]  # fmt: skip
    document = make_document(session)

    for target in path[1:]:
        transition(session, document, target, actor="tester")
    session.flush()

    assert _db_status(session, document) is S.POSTED
    moves = [(e.from_status, e.to_status) for e in _audit(session, document)]
    assert moves == list(itertools.pairwise(path))
