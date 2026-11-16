"""Approved documents held until Send (decision 98): approve, Send, Undo, and what they teach."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.adapters.storage.local import LocalStorage
from app.core.config import Settings
from app.domain.enums import DocumentStatus as S
from app.domain.enums import Role, SendMode
from app.models import AuditLog, Client, Document, Outbox, OutboxStatus, VendorMapping
from app.services import approval
from app.services.review import ReviewError
from tests.integration.factories import CLIENT, make_document
from tests.integration.test_approve import approve, mapping, outbox_rows
from tests.integration.test_posting import FakePosting, approved, send
from tests.integration.test_posting import xero as xero
from tests.integration.test_review_api import (  # noqa: F401  (fixtures)
    api,
    in_review,
    ok,
)
from tests.integration.test_verify import master_data, storage  # noqa: F401  (fixtures)
from tests.login import log_in

pytestmark = pytest.mark.integration

SETTINGS = Settings(_env_file=None, app_env="test")
ACTOR = "tester@missan.test"  # the logged-in user (tests/login.py)


def holds(session: Session) -> None:
    session.execute(
        update(Client).where(Client.client_id == CLIENT).values(send_mode=SendMode.ON_SEND)
    )


def learned(session: Session) -> VendorMapping | None:
    return session.scalars(select(VendorMapping).where(VendorMapping.client_id == CLIENT)).first()


def release(session: Session, *docs: Document | uuid.UUID) -> list[Outbox]:
    ids = [d if isinstance(d, uuid.UUID) else d.id for d in docs]
    return approval.release(session, client_id=CLIENT, document_ids=ids, settings=SETTINGS,
                            actor="sender@missan.test")  # fmt: skip


# --- Approve ----------------------------------------------------------------------------------


def test_approval_is_held_for_send_and_teaches_nothing_yet(
    api: TestClient,  # noqa: F811
    session: Session,
    storage: LocalStorage,  # noqa: F811
) -> None:
    holds(session)
    doc = in_review(session, storage)
    session.delete(mapping(session))  # a supplier never approved before
    session.flush()

    body = ok(approve(api, doc))

    assert body["status"] == "APPROVED"
    [row] = outbox_rows(session, doc)
    assert (row.status, row.released_by, row.released_at) == (OutboxStatus.HELD, None, None)
    assert api.app.state.posting_asked == []  # type: ignore[attr-defined]  # the worker waits
    assert learned(session) is None  # an approval can still be taken back
    held = ok(api.get("/documents", params={"client_id": CLIENT, "sending": "held"}))
    assert [d["document_id"] for d in held["documents"]] == [str(doc.id)]
    assert held["documents"][0]["sending"]["state"] == "held"


def test_sent_straight_away_releases_at_approval_and_learns_then(
    api: TestClient,  # noqa: F811
    session: Session,
    storage: LocalStorage,  # noqa: F811
) -> None:
    doc = in_review(session, storage)  # a client from before decision 98: AFTER_APPROVAL
    session.delete(mapping(session))
    session.flush()

    ok(approve(api, doc))

    [row] = outbox_rows(session, doc)
    assert (row.status, row.released_by) == (OutboxStatus.PENDING, ACTOR)
    assert row.released_at is not None
    assert api.app.state.posting_asked == [str(row.id)]  # type: ignore[attr-defined]
    assert learned(session) is not None


# --- Send -------------------------------------------------------------------------------------


def test_send_releases_exactly_the_documents_chosen_once_and_learns_then(
    api: TestClient,  # noqa: F811
    session: Session,
    storage: LocalStorage,  # noqa: F811
) -> None:
    holds(session)
    first = in_review(session, storage, "INV-00301")
    second = in_review(session, storage, "INV-00302")
    session.delete(mapping(session))
    session.flush()
    ok(approve(api, first))
    ok(approve(api, second))

    sent = release(session, first)
    again = release(session, first)  # pressed twice, or by two people: once is all

    assert [row.document_id for row in sent] == [first.id] and again == []
    [row] = outbox_rows(session, first)
    assert (row.status, row.released_by) == (OutboxStatus.PENDING, "sender@missan.test")
    [other] = outbox_rows(session, second)
    assert other.status is OutboxStatus.HELD  # not chosen: still waits
    assert learned(session) is not None  # the supplier's coding is learned once it is sent


def test_send_all_sends_every_waiting_approval_when_as_many_wait_as_were_seen(
    api: TestClient,  # noqa: F811
    session: Session,
    storage: LocalStorage,  # noqa: F811
) -> None:
    holds(session)
    first = in_review(session, storage, "INV-00301")
    second = in_review(session, storage, "INV-00302")
    ok(approve(api, first))

    with pytest.raises(approval.ListChangedError, match="1 approved documents wait for Send now"):
        approval.release_all(session, client_id=CLIENT, expected=2, settings=SETTINGS, actor=ACTOR)
    assert outbox_rows(session, first)[0].status is OutboxStatus.HELD  # nothing was sent

    ok(approve(api, second))  # now two wait, as the screen says
    sent = approval.release_all(session, client_id=CLIENT, expected=2, settings=SETTINGS,
                                actor=ACTOR)  # fmt: skip

    assert {row.document_id for row in sent} == {first.id, second.id}
    assert {row.status for row in sent} == {OutboxStatus.PENDING}


def test_send_leaves_alone_what_is_not_waiting_or_not_this_companys(
    session: Session,
    xero: FakePosting,
) -> None:
    holds(session)
    sent_already, _ = approved(session)  # its row is PENDING already
    other_company = make_document(session, client_id="other-company")

    assert release(session, sent_already, other_company.id, uuid.uuid4()) == []


def test_send_takes_at_most_a_page_of_documents_at_a_time(session: Session) -> None:
    with pytest.raises(ReviewError, match="at most 200"):
        release(session, *(uuid.uuid4() for _ in range(201)))


def test_a_held_document_is_never_sent_until_released(
    session: Session,
    xero: FakePosting,
) -> None:
    doc, outbox = approved(session)
    outbox.status = OutboxStatus.HELD
    outbox.next_attempt_at = datetime.now(UTC) - timedelta(days=1)  # long due, still held
    session.flush()

    assert send(session, xero) is None  # the worker finds nothing to send
    assert xero.bills == []

    release(session, doc)
    outcome = send(session, xero)

    assert outcome is not None and outcome.result == "posted"
    session.refresh(doc)
    assert doc.status is S.POSTED


# --- Undo -------------------------------------------------------------------------------------


def test_undo_takes_a_held_approval_back_to_review_and_it_can_be_approved_again(
    api: TestClient,  # noqa: F811
    session: Session,
    storage: LocalStorage,  # noqa: F811
) -> None:
    holds(session)
    doc = in_review(session, storage)
    session.delete(mapping(session))
    session.flush()
    ok(approve(api, doc))

    body = ok(api.post(f"/documents/{doc.id}/undo-approval"))

    assert body["status"] == "IN_REVIEW"
    assert outbox_rows(session, doc) == []  # never sent: nothing to keep
    assert learned(session) is None
    last = session.scalars(
        select(AuditLog).where(AuditLog.document_id == doc.id).order_by(AuditLog.id.desc())
    ).first()
    assert last is not None
    assert (last.from_status, last.to_status, last.actor, last.reason) == (
        S.APPROVED, S.IN_REVIEW, ACTOR, "approval undone before sending"
    )  # fmt: skip
    ok(approve(api, doc))
    release(session, doc)
    assert mapping(session).consistent_approvals == 1  # learned once, from what was sent


def test_undo_is_refused_once_sent(
    api: TestClient,  # noqa: F811
    session: Session,
    storage: LocalStorage,  # noqa: F811
) -> None:
    holds(session)
    doc = in_review(session, storage)
    ok(approve(api, doc))
    release(session, doc)

    refused = api.post(f"/documents/{doc.id}/undo-approval")
    in_review_doc = in_review(session, storage, "INV-00309")
    not_approved = api.post(f"/documents/{in_review_doc.id}/undo-approval")

    assert refused.status_code == 409
    assert "once sent, it is in the accounting system" in refused.json()["detail"]
    assert not_approved.status_code == 409
    session.refresh(doc)
    assert doc.status is S.APPROVED and outbox_rows(session, doc)[0].status is OutboxStatus.PENDING


def test_any_accountant_sends_and_undoes_but_only_an_admin_changes_the_setting(
    api: TestClient,  # noqa: F811
    session: Session,
    storage: LocalStorage,  # noqa: F811
) -> None:
    changed = ok(api.patch(f"/clients/{CLIENT}", json={"send_mode": "ON_SEND"}))
    assert changed["send_mode"] == "ON_SEND"
    log_in(api.app, Role.ACCOUNTANT)  # type: ignore[arg-type]
    doc = in_review(session, storage)
    ok(approve(api, doc))

    refused = api.patch(f"/clients/{CLIENT}", json={"send_mode": "AFTER_APPROVAL"})
    undone = api.post(f"/documents/{doc.id}/undo-approval")

    assert refused.status_code == 403
    assert undone.status_code == 200


def test_held_documents_are_counted_for_the_company_page(
    session: Session,
    xero: FakePosting,
) -> None:
    from app.services import posting

    _, outbox = approved(session)
    outbox.status = OutboxStatus.HELD
    session.flush()

    summary = posting.summary(session, CLIENT)

    assert (summary.held, summary.ready) == (1, 0)  # waiting for Send is not being sent
