"""Approve (task 4.8): re-check, APPROVED + outbox in one transaction, supplier mapping."""

from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.storage.local import LocalStorage
from app.domain.enums import DocumentStatus as S
from app.domain.enums import TaxType
from app.models import AuditLog, Document, Outbox, OutboxStatus, VendorMapping
from tests.integration.factories import CLIENT
from tests.integration.test_review_api import (  # noqa: F401  (fixtures)
    api,
    in_review,
    ok,
    patch,
)
from tests.integration.test_verify import TRN, master_data, storage  # noqa: F401

pytestmark = pytest.mark.integration


def approve(api: TestClient, doc: Document) -> object:  # noqa: F811
    return api.post(f"/documents/{doc.id}/approve")


def outbox_rows(session: Session, doc: Document) -> list[Outbox]:
    return list(session.scalars(select(Outbox).where(Outbox.document_id == doc.id)))


def mapping(session: Session) -> VendorMapping:
    return session.scalars(
        select(VendorMapping).where(
            VendorMapping.client_id == CLIENT, VendorMapping.vendor_key == f"TRN:{TRN}"
        )
    ).one()


def test_approve_moves_to_approved_with_an_outbox_row(
    api: TestClient,  # noqa: F811
    session: Session,
    storage: LocalStorage,  # noqa: F811
) -> None:
    doc = in_review(session, storage)

    body = ok(approve(api, doc))

    assert body["status"] == "APPROVED"
    [row] = outbox_rows(session, doc)
    assert (row.status, row.attempts, row.client_id) == (OutboxStatus.PENDING, 0, CLIENT)
    last = session.scalars(
        select(AuditLog).where(AuditLog.document_id == doc.id).order_by(AuditLog.id.desc())
    ).first()
    assert last is not None
    assert (last.from_status, last.to_status, last.actor) == (S.IN_REVIEW, S.APPROVED, "workbench")
    queue = ok(api.get("/documents", params={"client_id": CLIENT}))
    assert str(doc.id) not in {d["document_id"] for d in queue["documents"]}


def test_errors_block_approval_and_nothing_changes(
    api: TestClient,  # noqa: F811
    session: Session,
    storage: LocalStorage,  # noqa: F811
) -> None:
    doc = in_review(session, storage, total=Decimal("1060.00"))  # not printed, does not add up

    response = approve(api, doc)

    assert response.status_code == 422
    assert "must be fixed before approval" in response.json()["detail"]
    session.refresh(doc)
    assert doc.status is S.IN_REVIEW
    assert outbox_rows(session, doc) == []


def test_warnings_do_not_block(
    api: TestClient,  # noqa: F811
    session: Session,
    storage: LocalStorage,  # noqa: F811
) -> None:
    doc = in_review(session, storage, due_date=date(2026, 11, 20))  # due date not printed

    assert ok(approve(api, doc))["status"] == "APPROVED"


def test_only_documents_in_review_can_be_approved(
    api: TestClient,  # noqa: F811
    session: Session,
    storage: LocalStorage,  # noqa: F811
) -> None:
    approved = in_review(session, storage, "INV-00301")
    ok(approve(api, approved))
    parked = in_review(session, storage, "INV-00302")
    ok(patch(api, parked, document_type="NON_POSTABLE"))

    assert approve(api, approved).status_code == 409  # never twice: one outbox row
    assert approve(api, parked).status_code == 409
    assert len(outbox_rows(session, approved)) == 1


def test_the_same_supplier_coding_counts_one_more_consistent_approval(
    api: TestClient,  # noqa: F811
    session: Session,
    storage: LocalStorage,  # noqa: F811
) -> None:
    assert mapping(session).consistent_approvals == 3  # fixture: 5000, standard VAT
    doc = in_review(session, storage)  # coded 5000 from that history

    ok(approve(api, doc))

    learned = mapping(session)
    assert (learned.account_code, learned.tax_type, learned.consistent_approvals) == (
        "5000", TaxType.STANDARD_5, 4
    )  # fmt: skip
    assert learned.last_approved_at is not None


def test_a_different_supplier_coding_starts_over(
    api: TestClient,  # noqa: F811
    session: Session,
    storage: LocalStorage,  # noqa: F811
) -> None:
    doc = in_review(session, storage)
    lines = ok(api.get(f"/documents/{doc.id}"))["invoice"]["lines"]
    ok(patch(api, doc, lines=[{**line, "account_code": "5010"} for line in lines]))

    ok(approve(api, doc))

    assert (mapping(session).account_code, mapping(session).consistent_approvals) == ("5010", 1)


def test_mixed_accounts_teach_nothing(
    api: TestClient,  # noqa: F811
    session: Session,
    storage: LocalStorage,  # noqa: F811
) -> None:
    doc = in_review(session, storage)
    lines = ok(api.get(f"/documents/{doc.id}"))["invoice"]["lines"]
    lines[1] = {**lines[1], "account_code": "5010"}
    ok(patch(api, doc, lines=lines))

    ok(approve(api, doc))

    assert (mapping(session).account_code, mapping(session).consistent_approvals) == ("5000", 3)
