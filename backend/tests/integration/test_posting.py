"""Posting approved bills (tasks 5.1, 5.5) against a fake Xero: claim, local checks, outcomes,
retries and resuming. All values are made up."""

import json
from collections.abc import Callable, Iterator, MutableMapping
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.adapters.accounting.base import (
    AccessRevoked,
    ConnectorError,
    Posted,
    PostingAccessDenied,
    PostingBill,
    PostingRefused,
    PostingUnavailable,
)
from app.domain.enums import (
    AccountClass,
    AccountingProvider,
    AmountType,
    DocumentType,
    LineGrouping,
    TaxType,
)
from app.domain.enums import DocumentStatus as S
from app.domain.invoice import Invoice, Line
from app.models import AuditLog, Contact, Document, LedgerAccount, Outbox, OutboxStatus, TaxRate
from app.services import accounting_connections as connections
from app.services import posting, review
from app.services.clients import get_client
from app.services.review import SendingList
from tests.integration.factories import CLIENT, make_document
from tests.integration.test_xero_connection import SETTINGS, FakeXero, connect, registry
from tests.login import log_in

pytestmark = pytest.mark.integration


class FakePosting:
    """The PostingSystem port: records what it was asked, answers `outcome`."""

    def __init__(self) -> None:
        self.bills: list[PostingBill] = []
        self.started_with: list[dict[str, Any]] = []
        self.outcome: Exception | None = None
        self.gone: bool | Exception = True  # what is_deleted answers
        self.asked: list[str] = []

    def system(self, token: str, tenant_id: str) -> "FakePosting":
        assert (token, tenant_id) == ("access-1", "t2")
        return self

    def post_bill(
        self,
        bill: PostingBill,
        *,
        key: str,
        progress: MutableMapping[str, Any],
        save: Callable[[str], None],
    ) -> Posted:
        self.bills.append(bill)
        self.started_with.append(dict(progress))
        progress["contact_id"] = progress.get("contact_id", "xero-contact")
        save("contact")
        if self.outcome:
            raise self.outcome
        return Posted("xero-bill-1", Decimal("1050.00"), progress["contact_id"], True)

    def is_deleted(self, external_id: str) -> bool:
        self.asked.append(external_id)
        if isinstance(self.gone, Exception):
            raise self.gone
        return self.gone


class FakeStorage:
    """Every original is the same small made-up file."""

    def get(self, key: str) -> bytes:
        return b"%PDF-1.4 made-up"


@pytest.fixture
def xero(session: Session, tmp_path: Path) -> FakePosting:
    fake_auth = FakeXero()
    from tests.integration.factories import make_client

    make_client(session, tmp_path / "Cafe Al Noor")
    connect(session, fake_auth)
    client = get_client(session, CLIENT)
    assert client is not None
    client.accounting_provider = AccountingProvider.XERO
    session.add_all([
        LedgerAccount(client_id=CLIENT, provider=AccountingProvider.XERO, external_id="a1",
                      code="429", name="General Expenses", account_class=AccountClass.EXPENSE),
        TaxRate(client_id=CLIENT, provider=AccountingProvider.XERO, external_id="INPUT",
                code="INPUT", name="Tax on Purchases", rate=Decimal("5"),
                tax_type=TaxType.STANDARD_5),
    ])  # fmt: skip
    session.flush()
    return FakePosting()


def approved(session: Session, **changes: Any) -> tuple[Document, Outbox]:
    coded: dict[str, Any] = {"account_code": "429", "tax_type": TaxType.STANDARD_5}
    lines = [
        Line(description="Rice 5kg", qty=Decimal("2"), unit_price=Decimal("300.00"),
             amount=Decimal("600.00"), **coded),
        Line(description="Cooking oil", qty=Decimal("3"), unit_price=Decimal("133.33"),
             amount=Decimal("400.00"), **coded),
    ]  # fmt: skip
    invoice = Invoice(**{
        "document_type": DocumentType.BILL, "vendor_name": "Made-up Foods LLC",
        "vendor_trn": "100000000000003", "invoice_number": "INV-7",
        "invoice_date": date(2026, 10, 15), "currency": "AED",
        "amount_type": AmountType.EXCLUSIVE, "subtotal": Decimal("1000.00"),
        "vat": Decimal("50.00"), "total": Decimal("1050.00"), "lines": lines, **changes,
    })  # fmt: skip
    doc = make_document(session, status=S.APPROVED, document_type=DocumentType.BILL,
                        invoice=json.loads(invoice.model_dump_json()))  # fmt: skip
    outbox = Outbox(client_id=CLIENT, document_id=doc.id)
    session.add(outbox)
    session.flush()
    return doc, outbox


def send(session: Session, fake: FakePosting, **only: str) -> posting.Outcome | None:
    @contextmanager
    def sessions() -> Iterator[Session]:
        yield session
        session.flush()

    fake_xero = FakeXero()
    fake_xero.organisation = fake  # type: ignore[assignment]
    return posting.send_next(sessions, SETTINGS, registry(fake_xero), FakeStorage(), **only)


def test_an_approved_bill_is_posted_and_its_supplier_remembered(
    session: Session, xero: FakePosting
) -> None:
    doc, outbox = approved(session)

    outcome = send(session, xero)

    assert outcome is not None and outcome.result == "posted"
    (bill,) = xero.bills
    assert [(line.description, line.quantity, line.unit_amount, line.account_code, line.tax_code)
            for line in bill.lines] == [
        ("Rice 5kg", Decimal("2"), Decimal("300.00"), "429", "INPUT"),  # by item (decision 75)
        ("Cooking oil", Decimal("1"), Decimal("400.00"), "429", "INPUT"),
    ]  # fmt: skip
    assert (bill.supplier_name, bill.amount_type, bill.due_date) == (
        "Made-up Foods LLC", AmountType.EXCLUSIVE, None)  # fmt: skip
    assert bill.attachment is not None
    assert (bill.attachment.file_name, bill.attachment.mime_type) == ("inv.pdf", "application/pdf")
    session.refresh(doc)
    session.refresh(outbox)
    assert (doc.status, doc.external_id) == (S.POSTED, "xero-bill-1")
    assert (outbox.status, outbox.attempts, outbox.progress) == (
        OutboxStatus.DONE, 1, {"contact_id": "xero-contact"})  # fmt: skip
    contact = session.get(Contact, doc.contact_id)
    assert contact is not None and contact.external_id == "xero-contact"
    assert send(session, xero) is None  # nothing left


def test_lines_that_do_not_add_up_are_sent_as_one_line_of_the_printed_amounts(
    session: Session, xero: FakePosting
) -> None:
    """Decision 86: the oil read as 500.00 (1100.00 in all), 1000.00 + 50.00 VAT printed. No
    VAT type was chosen: the totals decide it."""
    coded: dict[str, Any] = {"account_code": "429", "tax_type": TaxType.STANDARD_5}
    lines = [Line(description="Rice 5kg", amount=Decimal("600.00"), **coded),
             Line(description="Cooking oil", amount=Decimal("500.00"), **coded)]  # fmt: skip
    approved(session, lines=lines, amount_type=None)

    outcome = send(session, xero)

    assert outcome is not None and outcome.result == "posted"
    (bill,) = xero.bills
    assert bill.amount_type is AmountType.EXCLUSIVE
    assert [(line.description, line.quantity, line.unit_amount, line.account_code, line.tax_code)
            for line in bill.lines] == [
        ("Rice 5kg; Cooking oil", Decimal(1), Decimal("1000.00"), "429", "INPUT"),
    ]  # fmt: skip


def test_an_account_not_in_xero_sends_it_back_to_review_without_calling_xero(
    session: Session, xero: FakePosting
) -> None:
    sample_account = Line(description="Rice", amount=Decimal("1000.00"), account_code="5010",
                          tax_type=TaxType.STANDARD_5)  # fmt: skip
    doc, outbox = approved(session, lines=[sample_account])

    outcome = send(session, xero)

    assert outcome is not None and outcome.result == "refused"
    assert xero.bills == []
    session.refresh(doc)
    assert doc.status is S.IN_REVIEW
    assert doc.failure_reason == (
        "Not sent to Xero: line 1: account 5010 is not in Xero's chart of accounts: choose another"
    )
    session.refresh(outbox)
    assert outbox.status is OutboxStatus.FAILED


def test_xero_refusing_sends_it_back_to_review_with_xeros_reason(
    session: Session, xero: FakePosting
) -> None:
    doc, _ = approved(session)
    xero.outcome = PostingRefused("bill INV-7 from this supplier is already in Xero (paid)")

    send(session, xero)

    session.refresh(doc)
    assert doc.status is S.IN_REVIEW
    assert doc.failure_reason is not None and "already in Xero (paid)" in doc.failure_reason


def test_a_busy_xero_is_tried_again_later_and_then_gives_up(
    session: Session, xero: FakePosting
) -> None:
    doc, outbox = approved(session)
    xero.outcome = PostingUnavailable("Xero's request limit was reached", retry_after=7)

    outcome = send(session, xero)

    assert outcome is not None and (outcome.result, outcome.retry_in) == ("retry", 7)
    session.refresh(outbox)
    session.refresh(doc)
    assert (outbox.status, doc.status) == (OutboxStatus.PENDING, S.POSTING)
    assert send(session, xero) is None  # not due yet

    outbox.attempts = posting.MAX_ATTEMPTS - 1
    outbox.next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
    session.flush()
    outcome = send(session, xero)
    assert outcome is not None and outcome.result == "dead"


def test_a_row_left_in_progress_by_a_crashed_worker_resumes_from_its_saved_steps(
    session: Session, xero: FakePosting
) -> None:
    doc, outbox = approved(session)
    xero.outcome = RuntimeError("worker killed")  # stands for a crash after the first sub-step
    assert send(session, xero).result == "dead"  # type: ignore[union-attr]
    long_ago = datetime.now(UTC) - timedelta(minutes=11)
    session.execute(update(Outbox).where(Outbox.id == outbox.id).values(
        status=OutboxStatus.IN_PROGRESS, updated_at=long_ago))  # fmt: skip
    xero.outcome = None

    outcome = send(session, xero)

    assert outcome is not None and outcome.result == "posted"
    assert xero.started_with[-1] == {"contact_id": "xero-contact"}  # resumed, not restarted
    session.refresh(doc)
    assert doc.status is S.POSTED


def test_only_bills_of_clients_using_xero_are_taken(session: Session, xero: FakePosting) -> None:
    credit_note, _ = approved(session, document_type=DocumentType.CREDIT_NOTE)
    session.execute(update(Document).where(Document.id == credit_note.id)
                    .values(document_type=DocumentType.CREDIT_NOTE))  # fmt: skip
    bill, _ = approved(session, invoice_number="INV-8")
    client = get_client(session, CLIENT)
    assert client is not None
    client.accounting_provider = AccountingProvider.MANUAL
    session.flush()

    assert send(session, xero) is None  # MANUAL: waits for the export (5.2)
    client.accounting_provider = AccountingProvider.XERO
    session.flush()
    send(session, xero)

    assert [b.invoice_number for b in xero.bills] == ["INV-8"]  # credit notes wait (5.7)
    assert posting.waiting(session, CLIENT) == 1
    assert session.scalars(select(Outbox.status).where(Outbox.document_id == bill.id)).one() is (
        OutboxStatus.DONE
    )


def test_a_receipt_is_posted_as_a_bill_due_on_its_own_date(
    session: Session, xero: FakePosting
) -> None:
    """Decision 50: Awaiting Approval like every bill; the payment is recorded in Xero."""
    receipt, _ = approved(
        session, document_type=DocumentType.RECEIPT, amount_type=AmountType.NO_TAX
    )
    session.execute(update(Document).where(Document.id == receipt.id)
                    .values(document_type=DocumentType.RECEIPT))  # fmt: skip

    outcome = send(session, xero)

    assert outcome is not None and outcome.result == "posted"
    (bill,) = xero.bills
    assert (bill.invoice_date, bill.due_date) == (date(2026, 10, 15), date(2026, 10, 15))
    assert posting.summary(session, CLIENT).not_yet == {}


def test_a_receipt_with_a_printed_due_date_keeps_it(session: Session, xero: FakePosting) -> None:
    receipt, _ = approved(session, document_type=DocumentType.RECEIPT, due_date=date(2026, 10, 20))
    session.execute(update(Document).where(Document.id == receipt.id)
                    .values(document_type=DocumentType.RECEIPT))  # fmt: skip

    send(session, xero)

    assert xero.bills[0].due_date == date(2026, 10, 20)


def test_the_summary_says_where_each_approved_document_is(
    session: Session, xero: FakePosting
) -> None:
    approved(session, invoice_number="INV-1")  # posted below
    send(session, xero)
    unknown_account = Line(description="Rice", amount=Decimal("1000.00"), account_code="5010",
                           tax_type=TaxType.STANDARD_5)  # fmt: skip
    approved(session, invoice_number="INV-2", lines=[unknown_account])
    send(session, xero)  # sent back: account not in Xero
    _, busy = approved(session, invoice_number="INV-3")
    xero.outcome = PostingUnavailable("Xero's request limit was reached")
    send(session, xero)  # waiting to try again
    approved(session, invoice_number="INV-4")  # ready
    credit_note, _ = approved(session, invoice_number="INV-5")
    session.execute(update(Document).where(Document.id == credit_note.id)
                    .values(document_type=DocumentType.CREDIT_NOTE))  # fmt: skip

    summary = posting.summary(session, CLIENT)

    assert (summary.ready, summary.retrying, summary.sent_back, summary.posted) == (1, 1, 1, 1)
    assert summary.not_yet == {"CREDIT_NOTE": 1}
    assert summary.retry_reason == "Xero's request limit was reached"
    session.refresh(busy)
    assert summary.retry_at == busy.next_attempt_at


# --- A connection that needs a new sign-in (decision 68) -----------------------------------------


def expiring(session: Session) -> None:
    """The stored access is about to expire: the next send must renew it."""
    connection = connections.connection_of(session, CLIENT)
    assert connection is not None
    connection.expires_at = datetime.now(UTC) + timedelta(seconds=30)
    session.flush()


def test_a_withdrawn_access_pauses_the_clients_bills_until_it_is_connected_again(
    session: Session, xero: FakePosting, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, outbox = approved(session)
    expiring(session)

    def withdrawn(self: FakeXero, refresh_token: str, region: str | None = None) -> None:
        raise AccessRevoked("Xero refused the sign-in (HTTP 400): invalid_grant")

    monkeypatch.setattr(FakeXero, "refresh", withdrawn)

    outcome = send(session, xero)

    assert outcome is not None and outcome.result == "paused"
    session.refresh(outbox)
    assert (outbox.status, outbox.attempts) == (OutboxStatus.PENDING, 0)  # no attempt counted
    connection = connections.connection_of(session, CLIENT)
    assert connection is not None and connection.needs_reconnect
    assert "invalid_grant" in (connection.reconnect_reason or "")
    assert send(session, xero) is None  # paused: the client is not even asked
    summary = posting.summary(session, CLIENT)
    assert (summary.paused, summary.ready) == (1, 0)
    assert summary.paused_reason == connection.reconnect_reason
    assert not xero.bills

    monkeypatch.undo()
    connect(session, FakeXero())  # someone signs in again

    outcome = send(session, xero)

    assert outcome is not None and outcome.result == "posted"
    assert connections.connection_of(session, CLIENT).needs_reconnect is False  # type: ignore[union-attr]


def test_xero_unreachable_while_renewing_is_tried_again_not_paused(
    session: Session, xero: FakePosting, monkeypatch: pytest.MonkeyPatch
) -> None:
    approved(session)
    expiring(session)

    def unreachable(self: FakeXero, refresh_token: str, region: str | None = None) -> None:
        raise ConnectorError("cannot reach Xero (ConnectTimeout)")

    monkeypatch.setattr(FakeXero, "refresh", unreachable)

    outcome = send(session, xero)

    assert outcome is not None and outcome.result == "retry"
    assert connections.connection_of(session, CLIENT).needs_reconnect is False  # type: ignore[union-attr]


def test_a_permission_refused_while_sending_pauses_the_client(
    session: Session, xero: FakePosting
) -> None:
    doc, outbox = approved(session)
    xero.outcome = PostingAccessDenied("Xero refused access (HTTP 403)", reconnect=True)

    outcome = send(session, xero)

    assert outcome is not None and outcome.result == "paused"
    session.refresh(outbox)
    session.refresh(doc)
    assert (outbox.status, outbox.attempts, doc.status) == (OutboxStatus.PENDING, 0, S.POSTING)
    assert connections.connection_of(session, CLIENT).needs_reconnect  # type: ignore[union-attr]


def test_an_access_token_refused_once_is_renewed_at_the_next_try(
    session: Session, xero: FakePosting
) -> None:
    _, outbox = approved(session)
    xero.outcome = PostingAccessDenied("Xero refused access (HTTP 401)", reconnect=False)

    outcome = send(session, xero)

    assert outcome is not None and outcome.result == "retry"
    connection = connections.connection_of(session, CLIENT)
    assert connection is not None and not connection.needs_reconnect
    assert connection.expires_at <= datetime.now(UTC)  # renewed before the next call
    session.refresh(outbox)
    assert outbox.attempts == 1


def test_bills_of_a_disconnected_client_are_paused_not_ready(
    session: Session, xero: FakePosting
) -> None:
    approved(session)
    connections.disconnect(session, SETTINGS, registry(FakeXero()), CLIENT)

    summary = posting.summary(session, CLIENT)

    assert (summary.paused, summary.ready) == (1, 0)
    assert summary.paused_reason is not None and "not connected" in summary.paused_reason


# --- A send that gave up (decision 69) ------------------------------------------------------------


def gave_up(session: Session, xero: FakePosting, **progress: Any) -> tuple[Document, Outbox]:
    """An approved bill whose send failed with an unknown error, after `progress` was saved."""
    doc, outbox = approved(session)
    xero.outcome = RuntimeError("made-up unknown error")
    assert send(session, xero).result == "dead"  # type: ignore[union-attr]
    outbox.progress = {**(outbox.progress or {}), **progress}
    session.flush()
    xero.outcome = None
    return doc, outbox


def test_a_send_that_gave_up_is_shown_as_failed_with_its_reason(
    session: Session, xero: FakePosting
) -> None:
    doc, _ = gave_up(session, xero)

    sending = posting.sending_of(session, doc)
    summary = posting.summary(session, CLIENT)

    assert sending is not None and sending.state == "failed"
    assert sending.reason == "unexpected error (RuntimeError)"
    assert sending.can_send_back  # the create request was never sent
    assert (summary.failed, summary.failed_reason) == (1, "unexpected error (RuntimeError)")
    assert send(session, xero) is None  # not tried again by itself


def test_try_again_resumes_with_the_same_key_and_posts_once(
    session: Session, xero: FakePosting
) -> None:
    doc, outbox = gave_up(session, xero, create_sent=True)

    row = posting.try_again(session, doc)
    outcome = send(session, xero, outbox_id=str(row.id))

    assert row.id == outbox.id  # same row, so the same idempotency key
    assert outcome is not None and outcome.result == "posted"
    assert xero.started_with[-1]["create_sent"] is True  # resumed from the saved progress
    session.refresh(doc)
    assert doc.status is S.POSTED
    assert posting.summary(session, CLIENT).failed == 0


def test_send_back_is_refused_once_the_create_request_was_sent(
    session: Session, xero: FakePosting
) -> None:
    doc, outbox = gave_up(session, xero, create_sent=True)

    assert posting.sending_of(session, doc).can_send_back is False  # type: ignore[union-attr]
    with pytest.raises(posting.SendError, match="use Try again"):
        posting.send_back(session, doc, actor="tester@missan.test")
    session.refresh(outbox)
    assert outbox.status is OutboxStatus.DEAD  # nothing changed


def test_send_back_before_the_create_request_returns_it_to_review(
    session: Session, xero: FakePosting
) -> None:
    doc, outbox = gave_up(session, xero)

    posting.send_back(session, doc, actor="tester@missan.test")

    session.refresh(outbox)
    session.refresh(doc)
    assert (outbox.status, doc.status) == (OutboxStatus.FAILED, S.IN_REVIEW)
    assert doc.failure_reason == (
        "Sent back after it could not be sent: unexpected error (RuntimeError)")  # fmt: skip
    assert posting.sending_of(session, doc) is None


def test_only_a_send_that_gave_up_can_be_tried_again_or_sent_back(
    session: Session, xero: FakePosting
) -> None:
    doc, _ = approved(session)

    with pytest.raises(posting.SendError):
        posting.try_again(session, doc)
    with pytest.raises(posting.SendError):
        posting.send_back(session, doc, actor="tester@missan.test")


def test_a_document_changed_since_approval_is_never_sent(
    session: Session, xero: FakePosting
) -> None:
    doc, _ = approved(session)
    session.execute(update(Document).where(Document.id == doc.id)
                    .values(status=S.IN_REVIEW))  # fmt: skip

    assert send(session, xero) is None
    assert not xero.bills


def test_the_review_screen_tries_again_or_sends_back_through_the_api(
    session: Session, xero: FakePosting
) -> None:
    from fastapi.testclient import TestClient

    from app.api.documents import get_enqueue_posting
    from app.core.config import get_settings
    from app.core.db import get_session
    from app.main import create_app

    asked: list[str] = []
    app = create_app(SETTINGS)
    log_in(app)
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_settings] = lambda: SETTINGS
    app.dependency_overrides[get_enqueue_posting] = lambda: asked.append
    sent_create, _ = gave_up(session, xero, create_sent=True)
    with TestClient(app) as api:
        failed = api.get(f"/documents/{sent_create.id}").json()["sending"]
        refused = api.post(f"/documents/{sent_create.id}/send-back")
        again = api.post(f"/documents/{sent_create.id}/send-again")
        not_failed = api.post(f"/documents/{sent_create.id}/send-again")

    assert failed == {"state": "failed", "reason": "unexpected error (RuntimeError)",
                      "next_try_at": None, "can_send_back": False}  # fmt: skip
    assert refused.status_code == 409 and "use Try again" in refused.json()["detail"]
    assert again.status_code == 200 and again.json()["sending"]["state"] == "waiting"
    assert len(asked) == 1  # the worker was asked to send that row
    assert not_failed.status_code == 409


# --- Where every approved document is: the queue's lists (decision 70) --------------------------


def one_of_each(session: Session, xero: FakePosting) -> dict[str, Document]:
    """A posted, a failed and a waiting bill, one back in review not sent, and one still in
    review."""
    posted, _ = approved(session, invoice_number="INV-1")
    send(session, xero)
    failed, _ = gave_up(session, xero)
    waiting, _ = approved(session, invoice_number="INV-3")
    in_review, _ = approved(session, invoice_number="INV-4")
    session.execute(update(Document).where(Document.id == in_review.id)
                    .values(status=S.IN_REVIEW))  # fmt: skip
    not_sent, _ = approved(session, invoice_number="INV-5")
    reason = "Not sent to Xero: made-up"
    session.execute(update(Document).where(Document.id == not_sent.id)
                    .values(status=S.IN_REVIEW, failure_reason=reason))  # fmt: skip
    session.expire_all()
    return {"posted": posted, "failed": failed, "waiting": waiting, "not_sent": not_sent}


def test_each_list_holds_its_approved_documents_and_counts_them_all(
    session: Session, xero: FakePosting
) -> None:
    docs = one_of_each(session, xero)

    for name in SendingList:
        page = review.list_queue(session, client_id=CLIENT, sending=name)

        assert [d.id for d in page.documents] == [docs[name.value].id], name
        assert page.sending_counts == {SendingList.WAITING: 1, SendingList.FAILED: 1,
                                       SendingList.POSTED: 1, SendingList.NOT_SENT: 1}  # fmt: skip
        assert page.counts == {}  # categories are for documents in review


def test_a_page_of_states_says_the_same_as_each_document_alone(
    session: Session, xero: FakePosting
) -> None:
    # Back in review is not on its way: it has no sending state.
    docs = [d for name, d in one_of_each(session, xero).items() if name != "not_sent"]

    together = posting.sending_states(session, docs)

    assert {d.id: posting.sending_of(session, d) for d in docs} == together
    assert sorted(s.state for s in together.values()) == ["failed", "posted", "waiting"]


def test_posted_rows_link_to_the_bill_in_xero_through_the_api(
    session: Session, xero: FakePosting
) -> None:
    from fastapi.testclient import TestClient

    from app.core.config import get_settings
    from app.core.db import get_session
    from app.main import create_app

    one_of_each(session, xero)
    connections.connection_of(session, CLIENT).org_ref = "!aB1cD"  # type: ignore[union-attr]
    session.flush()
    app = create_app(SETTINGS)
    log_in(app)
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_settings] = lambda: SETTINGS
    with TestClient(app) as api:
        posted = api.get("/documents", params={"client_id": CLIENT, "sending": "posted"}).json()
        failed = api.get("/documents", params={"client_id": CLIENT, "sending": "failed"}).json()

    [row] = posted["documents"]
    assert row["sending"]["state"] == "posted"
    assert row["external_url"].startswith(
        "https://go.xero.com/organisationlogin/default.aspx?shortcode=%21aB1cD"
    )
    assert row["external_url"].endswith("InvoiceID%3Dxero-bill-1")
    assert posted["sending_counts"] == {"waiting": 1, "failed": 1, "posted": 1, "not_sent": 1}
    [row] = failed["documents"]
    assert (row["sending"]["state"], row["external_url"]) == ("failed", None)


def test_not_sent_documents_come_first_in_review_with_their_reason(
    session: Session, xero: FakePosting
) -> None:
    from fastapi.testclient import TestClient

    from app.core.config import get_settings
    from app.core.db import get_session
    from app.domain.enums import QueueCategory
    from app.main import create_app

    exception = make_document(session, status=S.IN_REVIEW, queue=QueueCategory.EXCEPTION)
    xero.outcome = PostingRefused("Account code '429' has been archived")
    refused, _ = approved(session)
    send(session, xero)  # back in review with Xero's reason
    session.execute(update(Document).where(Document.id == refused.id)
                    .values(queue=QueueCategory.READY))  # fmt: skip
    session.expire_all()
    app = create_app(SETTINGS)
    log_in(app)
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_settings] = lambda: SETTINGS
    with TestClient(app) as api:
        page = api.get("/documents", params={"client_id": CLIENT}).json()

    # Ready, yet above the Exception: the accountant sees first what did not go through.
    assert [row["document_id"] for row in page["documents"]] == [str(refused.id), str(exception.id)]
    first, second = page["documents"]
    assert first["not_sent"] == "Not sent to Xero: Account code '429' has been archived"
    assert second["not_sent"] is None
    assert page["sending_counts"]["not_sent"] == 1
    assert posting.summary(session, CLIENT).sent_back == 1


def test_item_by_item_sends_identical_items_as_one_line_with_their_quantity(
    session: Session, xero: FakePosting
) -> None:
    """Decisions 73, 75: by item, the default; the setting copied on the outbox row at approval
    decides how its lines go."""
    coded: dict[str, Any] = {"account_code": "429", "tax_type": TaxType.STANDARD_5}
    masala = Line(description="Garam Masala", qty=Decimal(1), unit_price=Decimal("8.80"),
                  amount=Decimal("8.80"), **coded)  # fmt: skip
    yogurt = Line(description="Yogurt", qty=Decimal(1), unit_price=Decimal("6.95"),
                  amount=Decimal("6.95"), **coded)  # fmt: skip
    changes: dict[str, Any] = {"lines": [masala] * 3 + [yogurt] * 2,
                               "amount_type": AmountType.INCLUSIVE, "subtotal": Decimal("38.38"),
                               "vat": Decimal("1.92"), "total": Decimal("40.30")}  # fmt: skip
    _, by_item = approved(session, **changes)  # by item: the default
    _, by_account = approved(session, **changes, invoice_number="INV-8")
    by_account.line_grouping = LineGrouping.BY_ACCOUNT  # as copied at its approval
    session.flush()

    # Each row named: made in one transaction, both have the same time, so "next" is either.
    send(session, xero, outbox_id=str(by_item.id))
    send(session, xero, outbox_id=str(by_account.id))

    assert [(x.description, x.quantity, x.unit_amount) for x in xero.bills[0].lines] == [
        ("Garam Masala", Decimal(3), Decimal("8.80")),
        ("Yogurt", Decimal(2), Decimal("6.95")),
    ]
    assert [(x.quantity, x.unit_amount) for x in xero.bills[1].lines] == [
        (Decimal(1), Decimal("40.30"))
    ]  # by account, as when it was approved


def posted_doc(session: Session, xero: FakePosting) -> Document:
    doc, _ = approved(session)
    send(session, xero)
    session.refresh(doc)
    assert (doc.status, doc.external_id) == (S.POSTED, "xero-bill-1")
    return doc


def reopen(session: Session, xero: FakePosting, doc: Document) -> str:
    fake_xero = FakeXero()
    fake_xero.organisation = xero  # type: ignore[assignment]
    return posting.reopen_deleted(session, SETTINGS, registry(fake_xero), doc,
                                  actor="tester@missan.test")  # fmt: skip


def test_a_bill_deleted_in_the_accounting_system_comes_back_to_review(
    session: Session, xero: FakePosting
) -> None:
    doc = posted_doc(session, xero)

    name = reopen(session, xero, doc)

    assert (name, xero.asked) == ("Xero", ["xero-bill-1"])
    assert (doc.status, doc.external_id) == (S.IN_REVIEW, None)
    assert doc.failure_reason == "Deleted in Xero: change it if needed and approve again."
    [audit] = session.scalars(
        select(AuditLog).where(AuditLog.document_id == doc.id, AuditLog.to_status == S.IN_REVIEW)
    ).all()
    assert (audit.actor, audit.reason, audit.details) == (
        "tester@missan.test", "deleted in Xero", {"external_id": "xero-bill-1"})  # fmt: skip
    assert posting.sending_of(session, doc) is None
    assert posting.summary(session, CLIENT).sent_back == 1  # said on the Clients page too


def test_a_bill_still_in_the_accounting_system_is_never_sent_again(
    session: Session, xero: FakePosting
) -> None:
    doc = posted_doc(session, xero)
    xero.gone = False

    with pytest.raises(posting.SendError, match="still in Xero: delete or void it there first"):
        reopen(session, xero, doc)

    assert (doc.status, doc.external_id) == (S.POSTED, "xero-bill-1")


def test_when_the_accounting_system_cannot_be_asked_nothing_changes(
    session: Session, xero: FakePosting
) -> None:
    doc = posted_doc(session, xero)
    xero.gone = PostingUnavailable("cannot reach Xero (ConnectTimeout)")

    with pytest.raises(posting.SendError, match="could not be asked"):
        reopen(session, xero, doc)

    assert doc.status is S.POSTED


def test_only_a_posted_document_can_be_sent_again(session: Session, xero: FakePosting) -> None:
    doc, _ = approved(session)

    with pytest.raises(posting.SendError, match="only a document sent"):
        reopen(session, xero, doc)

    assert xero.asked == []
