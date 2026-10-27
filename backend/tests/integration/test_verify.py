"""The verify step end to end: every Phase 3 check, saved results, queue and status."""

import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.accounting.manual import ManualAccountingSystem
from app.adapters.ocr.base import RecognizedText
from app.adapters.storage.keys import ocr_key, page_image_key
from app.adapters.storage.local import LocalStorage
from app.core.config import Settings
from app.domain.enums import AmountType, DocumentType, QueueCategory, TaxType
from app.domain.enums import DocumentStatus as S
from app.domain.invoice import Invoice, Line
from app.models import AuditLog, Document, Extraction, VendorMapping
from app.pipeline.read import PageText, TextSpan, encode_png
from app.pipeline.verify.step import (
    VerifyOutcome,
    VerifyResult,
    recheck_document,
    verify_document,
)
from app.services.master_data import import_master_data
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
    assert {"totals_mismatch", "not_on_document"} <= codes(doc)
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


def test_a_charge_already_read_as_a_line_offers_the_subtotal_not_a_second_line(
    session: Session, storage: LocalStorage
) -> None:
    """Decision 49: the model put the delivery charge in the lines but kept the printed
    subtotal; adding the charge again would count it twice."""
    lines = [*bill().lines, Line(description="Delivery charge", amount=Decimal("15.99"))]
    doc = extracted(session, storage, bill(total=Decimal("1065.99"), lines=lines), WITH_DELIVERY)

    verify(session, storage, doc)

    [issue] = [i for i in doc.issues or [] if i["code"] == "subtotal_without_charges"]
    assert (issue["field"], issue["suggestion"]) == ("subtotal", "1015.99")
    assert "Delivery charge 15.99" in issue["message"]
    assert not codes(doc) & {"total_has_charges", "totals_mismatch", "lines_sum_mismatch"}


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

    assert "totals_mismatch" in codes(doc)
    assert "total_has_charges" not in codes(doc)
