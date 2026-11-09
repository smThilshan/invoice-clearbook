"""Approve (task 4.8): re-check, APPROVED + outbox in one transaction, supplier mapping."""

from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.storage.local import LocalStorage
from app.domain.enums import DocumentStatus as S
from app.domain.enums import DocumentType as T
from app.domain.enums import LineGrouping, Role, TaxType
from app.models import AuditLog, Document, Outbox, OutboxStatus, VendorMapping
from tests.integration.factories import CLIENT
from tests.integration.test_review_api import (  # noqa: F401  (fixtures)
    api,
    in_review,
    ok,
    patch,
)
from tests.integration.test_verify import (  # noqa: F401
    PETTY_CASH,
    TRN,
    master_data,
    sends_expenses,
    storage,
)
from tests.login import log_in

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
    assert api.app.state.posting_asked == [str(row.id)]  # type: ignore[attr-defined]  # after commit
    last = session.scalars(
        select(AuditLog).where(AuditLog.document_id == doc.id).order_by(AuditLog.id.desc())
    ).first()
    assert last is not None
    assert (last.from_status, last.to_status, last.actor) == (
        S.IN_REVIEW, S.APPROVED, "tester@missan.test"  # the logged-in user (decision 71)
    )  # fmt: skip
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


def test_a_document_approved_again_does_not_count_twice_for_its_supplier(
    api: TestClient,  # noqa: F811
    session: Session,
    storage: LocalStorage,  # noqa: F811
) -> None:
    """Decision 76: sent back, or deleted in the accounting system and sent again, it is still
    one document: its first approval taught the supplier's coding already."""
    doc = in_review(session, storage)
    session.add(Outbox(client_id=CLIENT, document_id=doc.id, status=OutboxStatus.DONE))
    session.flush()

    ok(approve(api, doc))

    assert mapping(session).consistent_approvals == 3  # unchanged
    assert len(outbox_rows(session, doc)) == 2  # a new row, a new key


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


def test_a_suppliers_first_approval_starts_its_memory(
    api: TestClient,  # noqa: F811
    session: Session,
    storage: LocalStorage,  # noqa: F811
) -> None:
    """Regression: the first approval of a supplier with no memory yet crashed (500)."""
    doc = in_review(session, storage)  # coded 5000
    session.delete(mapping(session))  # a supplier never approved before
    session.flush()

    ok(approve(api, doc))

    learned = mapping(session)
    assert (learned.account_code, learned.tax_type, learned.consistent_approvals) == (
        "5000", TaxType.STANDARD_5, 1
    )  # fmt: skip


def test_approval_keeps_the_clients_way_of_sending_lines_on_the_outbox_row(
    api: TestClient,  # noqa: F811
    session: Session,
    storage: LocalStorage,  # noqa: F811
) -> None:
    ok(api.patch(f"/clients/{CLIENT}", json={"line_grouping": "BY_ACCOUNT"}))
    doc = in_review(session, storage)

    ok(approve(api, doc))

    [row] = outbox_rows(session, doc)
    assert row.line_grouping is LineGrouping.BY_ACCOUNT


def test_only_an_admin_changes_how_lines_are_sent(api: TestClient) -> None:  # noqa: F811
    clients = ok(api.get("/clients"))
    assert [c["line_grouping"] for c in clients] == ["BY_ITEM"]  # the default (decision 75)
    log_in(api.app, Role.ACCOUNTANT)  # type: ignore[arg-type]

    refused = api.patch(f"/clients/{CLIENT}", json={"line_grouping": "BY_ITEM"})

    assert refused.status_code == 403
    assert api.patch("/clients/nobody", json={"line_grouping": "BY_ITEM"}).status_code == 403


# --- bill or expense (decision 88) ------------------------------------------------------


def test_approval_teaches_the_suppliers_bill_or_expense(
    api: TestClient,  # noqa: F811
    session: Session,
    storage: LocalStorage,  # noqa: F811
) -> None:
    doc = in_review(session, storage)  # "Due: 14/11/2026" printed: a bill

    ok(approve(api, doc))

    assert (mapping(session).payment_type, mapping(session).payment_approvals) == (T.BILL, 1)


def test_the_accountants_own_choice_is_trusted_at_once(
    api: TestClient,  # noqa: F811
    session: Session,
    storage: LocalStorage,  # noqa: F811
) -> None:
    doc = in_review(session, storage)
    ok(patch(api, doc, document_type="RECEIPT"))

    ok(approve(api, doc))

    assert (mapping(session).payment_type, mapping(session).payment_approvals) == (T.RECEIPT, 2)


def test_the_suppliers_memory_chooses_for_its_next_documents(
    session: Session,
    storage: LocalStorage,  # noqa: F811
) -> None:
    mapping(session).payment_type, mapping(session).payment_approvals = T.RECEIPT, 2
    session.flush()

    doc = in_review(session, storage)  # a due date printed, but the accountant said expense

    assert doc.document_type is T.RECEIPT
    assert "bill_or_expense" not in {issue["code"] for issue in doc.issues or []}


def test_one_approval_is_not_yet_a_memory(
    session: Session,
    storage: LocalStorage,  # noqa: F811
) -> None:
    mapping(session).payment_type, mapping(session).payment_approvals = T.RECEIPT, 1
    session.flush()

    assert in_review(session, storage).document_type is T.BILL


def test_the_accountants_type_is_never_chosen_again(
    api: TestClient,  # noqa: F811
    session: Session,
    storage: LocalStorage,  # noqa: F811
) -> None:
    doc = in_review(session, storage)  # a bill by its due date
    ok(patch(api, doc, document_type="RECEIPT"))

    body = ok(patch(api, doc, invoice_number="INV-00292"))  # checked again after another edit

    assert (body["document_type"], doc.document_type) == ("RECEIPT", T.RECEIPT)


# --- paid through (decision 89) ---------------------------------------------------------


def test_approval_teaches_the_supplier_s_paid_through_account(
    api: TestClient,  # noqa: F811
    session: Session,
    storage: LocalStorage,  # noqa: F811
) -> None:
    sends_expenses(session)
    doc = in_review(session, storage)
    body = ok(patch(api, doc, document_type="RECEIPT"))
    assert body["invoice"]["paid_through"] == PETTY_CASH  # the client's usual account

    ok(approve(api, doc))

    assert mapping(session).paid_through == PETTY_CASH


def test_the_accountant_s_paid_through_account_survives_a_save(
    api: TestClient,  # noqa: F811
    session: Session,
    storage: LocalStorage,  # noqa: F811
) -> None:
    sends_expenses(session, usual=None)
    doc = in_review(session, storage)
    ok(patch(api, doc, document_type="RECEIPT"))

    body = ok(patch(api, doc, paid_through=PETTY_CASH))
    again = ok(patch(api, doc, invoice_number="INV-00292"))

    assert "paid_through" in body["verified_fields"]
    assert again["invoice"]["paid_through"] == PETTY_CASH
    assert "paid_through_missing" not in {issue["code"] for issue in again["issues"]}
