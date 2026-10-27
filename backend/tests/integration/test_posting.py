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
    Posted,
    PostingBill,
    PostingRefused,
    PostingUnavailable,
)
from app.domain.enums import AccountClass, AccountingProvider, AmountType, DocumentType, TaxType
from app.domain.enums import DocumentStatus as S
from app.domain.invoice import Invoice, Line
from app.models import Contact, Document, LedgerAccount, Outbox, OutboxStatus, TaxRate
from app.services import posting
from app.services.clients import get_client
from tests.integration.factories import CLIENT, make_document
from tests.integration.test_xero_connection import SETTINGS, FakeXero, connect, registry

pytestmark = pytest.mark.integration


class FakePosting:
    """The PostingSystem port: records what it was asked, answers `outcome`."""

    def __init__(self) -> None:
        self.bills: list[PostingBill] = []
        self.started_with: list[dict[str, Any]] = []
        self.outcome: Exception | None = None

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
    assert [(line.quantity, line.unit_amount, line.account_code, line.tax_code)
            for line in bill.lines] == [
        (Decimal("2"), Decimal("300.00"), "429", "INPUT"),
        (Decimal("1"), Decimal("400.00"), "429", "INPUT"),  # 3 x 133.33 is not 400.00
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
