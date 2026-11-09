"""The verify step end to end: every Phase 3 check, saved results, queue and status."""

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.accounting.base import PAYS
from app.adapters.accounting.manual import ManualAccountingSystem
from app.adapters.accounting.zoho.auth import EXPENSE_SCOPE
from app.adapters.ocr.base import RecognizedText
from app.adapters.storage.keys import ocr_key, page_image_key
from app.adapters.storage.local import LocalStorage
from app.core.config import Settings
from app.domain.enums import (
    AccountClass,
    AccountingProvider,
    AmountType,
    Direction,
    DocumentType,
    ExpensesSentAs,
    QueueCategory,
    TaxType,
)
from app.domain.enums import DocumentStatus as S
from app.domain.invoice import Invoice, Line
from app.models import (
    AccountingConnection,
    AuditLog,
    Client,
    Correction,
    Document,
    Extraction,
    LedgerAccount,
    Run,
    VendorMapping,
)
from app.pipeline.read import PageText, TextSpan, encode_png
from app.pipeline.verify.step import (
    VerifyOutcome,
    VerifyResult,
    recheck_document,
    verify_document,
)
from app.services.corrections import sync_corrections
from app.services.master_data import import_master_data
from app.services.pipeline import recheck_in_review
from tests.integration.factories import CLIENT, make_document
from tests.integration.test_coding import FakeCoder
from tests.test_manual_master_data import SAMPLES

pytestmark = pytest.mark.integration

TRN = "100999000000001"  # samples: Al Waha Hypermarket LLC
PRINTED = """TAX INVOICE
Al Waha Hypermarket LLC   TRN 100-9990-0000-0001
Invoice No: INV-00291      Date: 15 Oct 2026      Due: 14/11/2026
Rice 5kg   600.00
Cooking oil   400.00
Sub Total   1,000.00
VAT 5%         50.00
Total AED   1,050.00"""
SETTINGS = Settings(_env_file=None)
TODAY = date(2026, 10, 21)  # folder date (factories) is 2026-10-20


@pytest.fixture
def storage(tmp_path: Path) -> LocalStorage:
    return LocalStorage(tmp_path / "store")


@pytest.fixture(autouse=True)
def master_data(session: Session) -> None:
    import_master_data(session, client_id=CLIENT,
                       source=ManualAccountingSystem.from_directory(SAMPLES))  # fmt: skip
    session.add(VendorMapping(client_id=CLIENT, vendor_key=f"TRN:{TRN}", account_code="5000",
                              tax_type=TaxType.STANDARD_5, consistent_approvals=3))  # fmt: skip
    session.flush()


def bill(**changes: Any) -> Invoice:
    fields: dict[str, Any] = {
        "document_type": DocumentType.BILL,
        "vendor_name": "Al Waha Hypermarket LLC",
        "vendor_trn": TRN,
        "invoice_number": "INV-00291",
        "invoice_date": date(2026, 10, 15),
        "due_date": date(2026, 11, 14),
        "currency": "AED",
        "amount_type": AmountType.EXCLUSIVE,  # as extraction sets it from these amounts (34)
        "subtotal": Decimal("1000.00"),
        "vat": Decimal("50.00"),
        "total": Decimal("1050.00"),
        "lines": [Line(description="Rice 5kg", amount=Decimal("600.00")),
                  Line(description="Cooking oil", amount=Decimal("400.00"))],
    }  # fmt: skip
    fields.update(changes)
    return Invoice(**fields)


def extracted(
    session: Session, storage: LocalStorage, invoice: Invoice | None, text: str = PRINTED
) -> Document:
    data = json.loads(invoice.model_dump_json()) if invoice else None
    doc = make_document(session, status=S.EXTRACTED, page_count=1, invoice=data,
                        document_type=invoice.document_type if invoice else None)  # fmt: skip
    lines = text.splitlines()
    spans = [TextSpan(line, (0.0, n / len(lines), 1.0, (n + 1) / len(lines)), None)
             for n, line in enumerate(lines)]  # fmt: skip
    storage.put(ocr_key(CLIENT, doc.id, 1), PageText(1, 1000, 1400, "text_layer", spans).to_json())
    return doc


def verify(session: Session, storage: LocalStorage, doc: Document) -> VerifyResult:
    return verify_document(session, storage, FakeCoder({"accounts": []}), doc,
                           settings=SETTINGS, today=TODAY)  # fmt: skip


def codes(doc: Document) -> set[str]:
    return {issue["code"] for issue in doc.issues or []}


def test_clean_bill_is_ready_coded_matched_and_in_review(
    session: Session, storage: LocalStorage
) -> None:
    doc = extracted(session, storage, bill())

    result = verify(session, storage, doc)

    assert (result.outcome, result.queue) == (VerifyOutcome.IN_REVIEW, QueueCategory.READY)
    assert (doc.status, doc.queue, doc.issues) == (S.IN_REVIEW, QueueCategory.READY, [])
    assert doc.contact_id is not None  # matched to the sample supplier by TRN
    assert (doc.vendor_key, doc.business_key) == (f"TRN:{TRN}", "N:291")
    assert doc.invoice is not None
    assert [line["account_code"] for line in doc.invoice["lines"]] == ["5000", "5000"]
    assert doc.coding is not None and doc.coding[0]["account_source"] == "supplier_history"
    moves = [(a.from_status, a.to_status) for a in session.scalars(
        select(AuditLog).where(AuditLog.document_id == doc.id).order_by(AuditLog.id))]  # fmt: skip
    assert moves == [(S.EXTRACTED, S.VERIFIED), (S.VERIFIED, S.IN_REVIEW)]


def test_only_warnings_go_to_check(session: Session, storage: LocalStorage) -> None:
    doc = extracted(session, storage, bill(due_date=date(2026, 11, 20)))  # not printed

    result = verify(session, storage, doc)

    assert result.queue is QueueCategory.CHECK
    assert (result.errors, result.warnings) == (0, 1)


def test_any_error_goes_to_exception(session: Session, storage: LocalStorage) -> None:
    doc = extracted(session, storage, bill(total=Decimal("1060.00")))

    result = verify(session, storage, doc)

    assert result.queue is QueueCategory.EXCEPTION
    assert {"vat_rate", "not_on_document"} <= codes(doc)
    assert doc.status is S.IN_REVIEW


def test_unreadable_extraction_still_reaches_review(
    session: Session, storage: LocalStorage
) -> None:
    doc = extracted(session, storage, None)
    session.add(Extraction(client_id=CLIENT, document_id=doc.id, attempt=1, model="m",
                           prompt_version="p", schema_version=1, raw_output="{}",
                           error="1 schema error(s): document_type:missing"))  # fmt: skip
    session.flush()

    result = verify(session, storage, doc)

    assert (doc.status, result.queue) == (S.IN_REVIEW, QueueCategory.EXCEPTION)
    [issue] = doc.issues or []
    assert issue["code"] == "extraction_invalid" and "document_type:missing" in issue["message"]


def test_second_copy_is_a_duplicate_exception(session: Session, storage: LocalStorage) -> None:
    verify(session, storage, extracted(session, storage, bill()))
    copy = extracted(session, storage, bill(invoice_number="inv 291"))

    result = verify(session, storage, copy)

    assert result.queue is QueueCategory.EXCEPTION
    assert "duplicate" in codes(copy)
    assert copy.business_key is None  # the original keeps the key


def test_quotation_confirmed_by_title_is_parked(session: Session, storage: LocalStorage) -> None:
    quote = Invoice(document_type=DocumentType.NON_POSTABLE, vendor_name="Al Waha Hypermarket LLC",
                    lines=[Line(description="Rice 5kg", amount=Decimal("600.00"))])  # fmt: skip
    doc = extracted(session, storage, quote, text="QUOTATION\nRice 5kg 600.00")

    result = verify(session, storage, doc)

    assert result.outcome is VerifyOutcome.PARKED
    assert doc.status is S.NON_POSTABLE
    assert doc.invoice is not None and doc.invoice["lines"][0]["account_code"] is None
    assert doc.coding == []


def test_only_extracted_documents_are_verified(session: Session, storage: LocalStorage) -> None:
    doc = extracted(session, storage, bill())
    verify(session, storage, doc)

    assert verify(session, storage, doc).outcome is VerifyOutcome.SKIPPED


# --- charges outside the lines (task 4.13) -------------------------------------------------

WITH_DELIVERY = PRINTED.replace(
    "VAT 5%         50.00\nTotal AED   1,050.00",
    "VAT 5%         50.00\nDelivery charge   15.99\nTotal AED   1,065.99",
)


class FakeOcr:
    """Our own OCR: reads the page cleanly where the scanner's text lost an amount."""

    def __init__(self, lines: list[str]) -> None:
        self.lines, self.calls = lines, 0

    def recognize(self, image: object) -> list[RecognizedText]:
        self.calls += 1
        return [RecognizedText(text, 0.99, (10.0, 40.0 * n, 900.0, 40.0 * n + 20))
                for n, text in enumerate(self.lines, start=1)]  # fmt: skip


def test_charges_printed_outside_the_lines_explain_the_total(
    session: Session, storage: LocalStorage
) -> None:
    doc = extracted(session, storage, bill(total=Decimal("1065.99")), WITH_DELIVERY)

    verify(session, storage, doc)

    [issue] = [i for i in doc.issues or [] if i["code"] == "total_has_charges"]
    assert issue["severity"] == "ERROR"
    assert issue["charges"] == [{"label": "Delivery charge", "amount": "15.99"}]
    assert not codes(doc) & {"totals_mismatch", "lines_sum_mismatch"}


def test_a_charge_already_read_as_a_line_needs_no_subtotal_and_no_second_line(
    session: Session, storage: LocalStorage
) -> None:
    """Decision 81 (was 49): the model put the delivery charge in the lines; the subtotal is
    the total less the VAT, so it holds the charge already. Nothing to add twice. The VAT
    printed is 5% of the items only: the charge carries none, which its VAT type must say."""
    lines = [*bill().lines, Line(description="Delivery charge", amount=Decimal("15.99"))]
    doc = extracted(session, storage, bill(total=Decimal("1065.99"), lines=lines), WITH_DELIVERY)

    verify(session, storage, doc)

    assert doc.invoice is not None and doc.invoice["subtotal"] == "1015.99"
    assert not codes(doc) & {"total_has_charges", "lines_sum_mismatch", "not_on_document"}
    assert "vat_rate" in codes(doc)  # the charge still counted as taxed
    untaxed = [*bill().lines, Line(description="Delivery charge", amount=Decimal("15.99"),
                                   tax_type=TaxType.NONE)]  # fmt: skip
    doc.invoice = json.loads(bill(total=Decimal("1065.99"), lines=untaxed).model_dump_json())
    recheck_document(session, storage, doc, settings=SETTINGS, today=TODAY)
    assert "vat_rate" not in codes(doc)


def test_our_own_ocr_reads_a_picture_whose_scanner_text_lost_the_charge(
    session: Session, storage: LocalStorage
) -> None:
    lost = WITH_DELIVERY.replace("Delivery charge   15.99", "Delivery charge")
    doc = extracted(session, storage, bill(total=Decimal("1065.99")), lost)
    scanned = PageText.from_json(storage.get(ocr_key(CLIENT, doc.id, 1)))
    scanned.source = "ocr"  # a phone scan: its text came from the scanner's OCR
    storage.put(ocr_key(CLIENT, doc.id, 1), scanned.to_json())
    storage.put(
        page_image_key(CLIENT, doc.id, 1), encode_png(np.full((1500, 1000, 3), 255, np.uint8))
    )
    ocr = FakeOcr(WITH_DELIVERY.splitlines())

    verify_document(session, storage, FakeCoder({"accounts": []}), doc, settings=SETTINGS,
                    today=TODAY, ocr=lambda: ocr)  # fmt: skip
    again = recheck_document(session, storage, doc, settings=SETTINGS, today=TODAY)

    assert "total_has_charges" in codes(doc)
    assert ocr.calls == 1  # made once and kept: the re-check reads the kept text
    assert again.errors == 1 and "total_has_charges" in codes(doc)


def test_a_total_nothing_explains_keeps_its_error(session: Session, storage: LocalStorage) -> None:
    doc = extracted(session, storage, bill(total=Decimal("1065.99")))

    verify_document(session, storage, FakeCoder({"accounts": []}), doc, settings=SETTINGS,
                    today=TODAY, ocr=lambda: FakeOcr(["nothing useful"]))  # fmt: skip

    assert "vat_rate" in codes(doc)  # the error: VAT not 5% of the total less the VAT
    assert "total_has_charges" not in codes(doc)


# --- a likely VAT type (decision 84) -------------------------------------------------------

HALFWAY = [Line(description="Rice 5kg", amount=Decimal("625.00")),
           Line(description="Cooking oil", amount=Decimal("400.00"))]  # fmt: skip
# 1025.00: 25.00 from the total and from the amount before VAT, so the amounts prove nothing


def halfway_bill(**changes: Any) -> Invoice:
    """VAT a fils off 5%: the totals cannot be sent as one line either (decision 86)."""
    return bill(lines=HALFWAY, vat=Decimal("50.01"), **changes)


def earlier(session: Session, invoice: Invoice, **fields: Any) -> Document:
    """Another document of the same supplier, already checked."""
    return make_document(session, status=S.IN_REVIEW, vendor_key=f"TRN:{TRN}",
                         invoice=json.loads(invoice.model_dump_json()), **fields)  # fmt: skip


def test_the_supplier_s_proven_vat_type_fills_an_empty_one_with_a_warning(
    session: Session, storage: LocalStorage
) -> None:
    gross = [Line(description="Rice 5kg", amount=Decimal("1050.00"))]
    earlier(session, bill(lines=gross, amount_type=AmountType.INCLUSIVE))  # its amounts prove it
    doc = extracted(session, storage, halfway_bill(amount_type=None))

    verify(session, storage, doc)

    assert doc.invoice is not None and doc.invoice["amount_type"] == "INCLUSIVE"
    [note] = [i for i in doc.issues or [] if i["code"] == "vat_type_likely"]
    assert note["severity"] == "WARNING" and "supplier" in note["message"]
    assert "vat_type_missing" not in codes(doc)


def test_a_likely_type_is_never_proof_for_the_next_document(
    session: Session, storage: LocalStorage
) -> None:
    earlier(session, halfway_bill(amount_type=AmountType.INCLUSIVE))  # chosen, unproven
    doc = extracted(session, storage, halfway_bill(amount_type=None))

    verify(session, storage, doc)

    assert doc.invoice is not None and doc.invoice["amount_type"] is None
    assert "vat_type_missing" in codes(doc)


def test_an_approved_or_accountant_chosen_type_is_proof(
    session: Session, storage: LocalStorage
) -> None:
    earlier(session, halfway_bill(amount_type=AmountType.EXCLUSIVE),
            verified_fields=["amount_type"])  # fmt: skip
    doc = extracted(session, storage, halfway_bill(amount_type=None))

    verify(session, storage, doc)

    assert doc.invoice is not None and doc.invoice["amount_type"] == "EXCLUSIVE"


def test_the_warning_goes_once_the_accountant_confirms_the_type(
    session: Session, storage: LocalStorage
) -> None:
    earlier(session, bill(lines=[Line(description="Rice", amount=Decimal("1050.00"))],
                          amount_type=AmountType.INCLUSIVE))  # fmt: skip
    doc = extracted(session, storage, halfway_bill(amount_type=None))
    verify(session, storage, doc)
    assert "vat_type_likely" in codes(doc)

    doc.verified_fields = ["amount_type"]
    recheck_document(session, storage, doc, settings=SETTINGS, today=TODAY)

    assert "vat_type_likely" not in codes(doc)
    assert doc.invoice is not None and doc.invoice["amount_type"] == "INCLUSIVE"


# --- the document date from every clue (decision 85) ---------------------------------------


def with_model_answer(session: Session, doc: Document, invoice: Invoice) -> None:
    session.add(Extraction(client_id=CLIENT, document_id=doc.id, attempt=1, model="m",
                           prompt_version="p", schema_version=2, raw_output="{}",
                           data=json.loads(invoice.model_dump_json())))  # fmt: skip
    session.flush()


def test_a_date_the_model_missed_is_read_from_the_page_with_a_note(
    session: Session, storage: LocalStorage
) -> None:
    missed = bill(invoice_date=None)
    doc = extracted(session, storage, missed)
    with_model_answer(session, doc, missed)

    verify(session, storage, doc)

    assert doc.invoice is not None and doc.invoice["invoice_date"] == "2026-10-15"
    [note] = [i for i in doc.issues or [] if i["code"] == "date_resolved"]
    assert note["severity"] == "WARNING" and "read from 'Date' on the page" in note["message"]
    assert "date_missing" not in codes(doc)

    recheck_document(session, storage, doc, settings=SETTINGS, today=TODAY)
    assert "date_resolved" in codes(doc)  # weighed from the model's answer: the note stays

    doc.verified_fields = ["invoice_date"]
    recheck_document(session, storage, doc, settings=SETTINGS, today=TODAY)
    assert "date_resolved" not in codes(doc)
    assert doc.invoice["invoice_date"] == "2026-10-15"


def test_a_date_the_checks_filled_is_not_the_accountant_s_correction(
    session: Session, storage: LocalStorage
) -> None:
    missed = bill(invoice_date=None)
    doc = extracted(session, storage, missed)
    with_model_answer(session, doc, missed)
    verify(session, storage, doc)

    sync_corrections(session, doc, corrected_by="accountant@example.com")

    assert not session.scalars(select(Correction).where(Correction.document_id == doc.id)).all()


def test_a_model_date_that_fits_is_never_touched(session: Session, storage: LocalStorage) -> None:
    doc = extracted(session, storage, bill())
    with_model_answer(session, doc, bill())

    verify(session, storage, doc)

    assert "date_resolved" not in codes(doc)
    assert doc.invoice is not None and doc.invoice["invoice_date"] == "2026-10-15"


def test_recheck_gives_documents_in_review_their_own_folder_date(
    session: Session, storage: LocalStorage
) -> None:
    run = Run(client_id=CLIENT, direction=Direction.PURCHASE, folder="October 2026",
              folder_date=date(2026, 10, 31))  # from the run's folder: the month end  # fmt: skip
    session.add(run)
    session.flush()
    doc = extracted(session, storage, bill())
    doc.run_id, doc.source_path, doc.folder_date = run.id, "October 2026/16/inv.pdf", None
    verify(session, storage, doc)

    checked, moved = recheck_in_review(session, storage, settings=SETTINGS, today=TODAY)

    assert (checked, moved) == (1, 1)
    assert doc.folder_date == date(2026, 10, 16)
    assert doc.issues == []  # checked again with it: 15 October is just before the folder


# --- paid through (decision 89) ---------------------------------------------------------

PETTY_CASH = "1090"


def sends_expenses(session: Session, *, usual: str | None = PETTY_CASH, scopes: str = "") -> None:
    """The client sends expenses as expenses, connected to Zoho with the permission for it
    (unless `scopes` says otherwise), and has a petty cash account to pay them through."""
    session.add(LedgerAccount(client_id=CLIENT, provider=AccountingProvider.MANUAL,
                              external_id="pc", code=PETTY_CASH, name="Petty Cash",
                              account_class=AccountClass.ASSET, raw={PAYS: "Cash"}))  # fmt: skip
    session.add(AccountingConnection(
        client_id=CLIENT, provider=AccountingProvider.ZOHO, tenant_id="905001",
        tenant_name="Made-up Trading", access_token="x", refresh_token="x",
        expires_at=datetime.now(UTC), scopes=scopes or f"ZohoBooks.bills.ALL {EXPENSE_SCOPE}",
    ))  # fmt: skip
    client = session.scalars(select(Client).where(Client.client_id == CLIENT)).first()
    if client is None:
        client = Client(client_id=CLIENT, name="Cafe Al Noor")
        session.add(client)
    client.expenses_sent_as, client.paid_through = ExpensesSentAs.EXPENSE, usual
    session.flush()


def an_expense(session: Session, storage: LocalStorage, **changes: Any) -> Document:
    doc = extracted(session, storage, bill(document_type=DocumentType.RECEIPT, due_date=None,
                                           **changes))  # fmt: skip
    doc.verified_fields = ["document_type"]  # the accountant's own choice: an expense
    return doc


def test_an_expense_is_paid_through_the_clients_usual_account(
    session: Session, storage: LocalStorage
) -> None:
    sends_expenses(session)
    doc = an_expense(session, storage)

    verify(session, storage, doc)

    assert doc.invoice is not None and doc.invoice["paid_through"] == PETTY_CASH
    assert not codes(doc) & {"paid_through_missing", "paid_through_invalid"}


def test_an_expense_with_no_account_to_pay_it_cannot_be_approved(
    session: Session, storage: LocalStorage
) -> None:
    sends_expenses(session, usual=None)
    doc = an_expense(session, storage)

    verify(session, storage, doc)

    assert "paid_through_missing" in codes(doc)
    assert doc.queue is QueueCategory.EXCEPTION


def test_the_suppliers_last_account_comes_first(session: Session, storage: LocalStorage) -> None:
    sends_expenses(session, usual=None)
    mapping = session.scalars(select(VendorMapping)).one()
    mapping.paid_through = PETTY_CASH
    doc = an_expense(session, storage)

    verify(session, storage, doc)

    assert doc.invoice is not None and doc.invoice["paid_through"] == PETTY_CASH


def test_without_the_permission_expenses_stay_bills_with_no_account(
    session: Session, storage: LocalStorage
) -> None:
    sends_expenses(session, scopes="ZohoBooks.bills.ALL")
    doc = an_expense(session, storage)

    verify(session, storage, doc)

    assert doc.invoice is not None and doc.invoice["paid_through"] is None
    assert "paid_through_missing" not in codes(doc)


def test_the_paid_through_account_is_never_the_model_s_correction(
    session: Session, storage: LocalStorage
) -> None:
    sends_expenses(session)
    doc = an_expense(session, storage)
    with_model_answer(session, doc, bill(document_type=DocumentType.RECEIPT, due_date=None))
    verify(session, storage, doc)
    doc.verified_fields = ["document_type", "paid_through"]

    sync_corrections(session, doc, corrected_by="accountant@example.com")

    fields = {c.field for c in session.scalars(select(Correction))}
    assert "paid_through" not in fields
