import json
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.llm import (
    ExtractionError,
    ExtractionRequest,
    ExtractionResult,
    ModelStatus,
)
from app.adapters.storage.keys import ocr_key
from app.adapters.storage.local import LocalStorage
from app.domain.enums import DocumentStatus, DocumentType
from app.models import AuditLog, Document, Extraction
from app.pipeline.extract import PROMPT_VERSION, ExtractOutcome, extract_document
from app.pipeline.no_vat import cleared_vat
from app.pipeline.read import PageText, TextSpan
from tests.integration.test_read import captured, run_read
from tests.read_fixtures import digital_pdf
from tests.test_read_pages import FakeOcr

pytestmark = pytest.mark.integration

VALID = json.dumps(
    {
        "document_type": "BILL",
        "vendor_name": "Al Waha Hypermarket LLC",
        "invoice_number": "INV-00291",
        "invoice_date": "2025-09-12",
        "subtotal": 1000.10,
        "vat": 50.00,
        "total": 1050.10,
        "currency": "AED",
        "lines": [{"description": "Food", "qty": None, "unit_price": None, "amount": 1000.10}],
        "evidence": {"total": "Total AED 1,050.10"},
    }
)


class FakeClient:
    model = "fake-model:1b"

    def __init__(self, raw: str = VALID, *, fail: bool = False) -> None:
        self.raw, self.fail = raw, fail
        self.requests: list[ExtractionRequest] = []

    def extract(
        self, request: ExtractionRequest, *, should_stop: object = None
    ) -> ExtractionResult:
        self.requests.append(request)
        if self.fail:
            raise ExtractionError("model timed out after 600s")
        return ExtractionResult(self.model, self.raw, False, 1234, 2000, 150, 1000, 200)

    def check(self) -> ModelStatus:
        return ModelStatus.OK

    def close(self) -> None:
        pass


@pytest.fixture
def storage(tmp_path: Path) -> LocalStorage:
    return LocalStorage(tmp_path / "store")


def read_doc(session: Session, storage: LocalStorage, pages: int = 1) -> Document:
    doc = captured(session, storage, digital_pdf(pages=pages), "application/pdf")
    run_read(session, storage, doc, FakeOcr())
    return doc


def extractions(session: Session, doc: Document) -> list[Extraction]:
    return list(session.scalars(select(Extraction).where(Extraction.document_id == doc.id)))


def test_valid_answer_becomes_working_copy(session: Session, storage: LocalStorage) -> None:
    doc = read_doc(session, storage)
    client = FakeClient()

    result = extract_document(session, storage, client, doc, max_images=3)

    assert (result.outcome, result.attempt) == (ExtractOutcome.EXTRACTED, 1)
    assert doc.status is DocumentStatus.EXTRACTED
    assert doc.document_type is DocumentType.BILL
    assert doc.invoice is not None
    assert Decimal(doc.invoice["total"]) == Decimal("1050.10")  # stored as exact text, no float
    [row] = extractions(session, doc)
    assert (row.model, row.prompt_version, row.error) == ("fake-model:1b", PROMPT_VERSION, None)
    assert row.raw_output == VALID and row.data == doc.invoice
    [request] = client.requests
    assert "INV-00291" in request.prompt and len(request.images) == 1


def test_invalid_answer_is_kept_and_flagged(session: Session, storage: LocalStorage) -> None:
    doc = read_doc(session, storage)

    result = extract_document(session, storage, FakeClient('{"total": "lots"}'), doc, max_images=3)

    assert result.outcome is ExtractOutcome.INVALID
    assert doc.status is DocumentStatus.EXTRACTED  # moves on; review flags it
    assert doc.invoice is None and doc.document_type is None
    [row] = extractions(session, doc)
    assert row.data is None and row.raw_output == '{"total": "lots"}'
    assert row.error is not None and "document_type:missing" in row.error
    entry = session.scalars(
        select(AuditLog).where(AuditLog.to_status == DocumentStatus.EXTRACTED)
    ).one()
    assert entry.reason == row.error
    assert entry.details["valid"] is False and entry.details["writing_ms"] == 200


def test_model_failure_writes_nothing(session: Session, storage: LocalStorage) -> None:
    doc = read_doc(session, storage)

    with pytest.raises(ExtractionError):
        extract_document(session, storage, FakeClient(fail=True), doc, max_images=3)

    assert doc.status is DocumentStatus.READ
    assert extractions(session, doc) == []


def test_only_first_pages_get_images(session: Session, storage: LocalStorage) -> None:
    doc = read_doc(session, storage, pages=3)
    client = FakeClient()

    extract_document(session, storage, client, doc, max_images=2)

    [request] = client.requests
    assert len(request.images) == 2
    assert request.prompt.count("<page number=") == 3
    assert "first 2 of 3 pages" in request.prompt


def test_not_read_document_is_skipped(session: Session, storage: LocalStorage) -> None:
    doc = read_doc(session, storage)
    extract_document(session, storage, FakeClient(), doc, max_images=3)
    client = FakeClient()

    result = extract_document(session, storage, client, doc, max_images=3)

    assert result.outcome is ExtractOutcome.SKIPPED
    assert client.requests == []
    assert len(extractions(session, doc)) == 1


def test_vat_on_a_page_that_is_no_tax_invoice_is_cleared_and_shown(
    session: Session, storage: LocalStorage
) -> None:
    """Decision 52: an app screen prints neither VAT nor a supplier TRN."""
    doc = read_doc(session, storage)
    screen = PageText.from_json(storage.get(ocr_key(doc.client_id, doc.id, 1)))
    screen.spans = [TextSpan("Order Details  Order ID: INV-00291  Total 1,050.10  Paid",
                             (0, 0, 1, 0.1), None)]  # fmt: skip
    storage.put(ocr_key(doc.client_id, doc.id, 1), screen.to_json())

    order = json.loads(VALID) | {
        "document_type": "RECEIPT",
        "lines": [{"description": "Food", "qty": None, "unit_price": None, "amount": 1000.10},
                  {"description": "Service fee", "qty": None, "unit_price": None, "amount": 50.00}],
    }  # fmt: skip

    extract_document(session, storage, FakeClient(json.dumps(order)), doc, max_images=3)

    assert doc.invoice is not None
    assert (doc.invoice["vat"], doc.invoice["amount_type"]) == (None, "NO_TAX")
    [row] = extractions(session, doc)
    assert cleared_vat(row.raw_output, row.data) == "50.0"


def test_vat_on_a_tax_invoice_is_kept(session: Session, storage: LocalStorage) -> None:
    doc = read_doc(session, storage)  # its page says "TAX INVOICE"

    extract_document(session, storage, FakeClient(), doc, max_images=3)

    assert doc.invoice is not None and Decimal(doc.invoice["vat"]) == Decimal("50.00")


def test_summary_rows_read_as_lines_are_removed_from_the_printed_summary(
    session: Session, storage: LocalStorage
) -> None:
    """Decision 56: UHM's cash and VAT read as items, the total left empty."""
    doc = read_doc(session, storage)
    slip = PageText.from_json(storage.get(ocr_key(doc.client_id, doc.id, 1)))
    slip.spans = [TextSpan(row, (0, n / 6, 1, (n + 0.5) / 6), None) for n, row in enumerate([
        "TAX INVOICE  TRN:100228932800003", "PENNE 3 5.99 17.97", "BEEF CUBE 2 21.99 93.31",
        "CASH 115.00", "hount: 105.98  ISvOd 5.30  Bi ount: 111.28", "can punt 115.00  Bal 3.72",
    ])]  # fmt: skip
    storage.put(ocr_key(doc.client_id, doc.id, 1), slip.to_json())
    answer = json.loads(VALID) | {
        "document_type": "RECEIPT", "subtotal": 115.00, "vat": 5.30, "total": None,
        "lines": [{"description": d, "qty": None, "unit_price": None, "amount": a}
                  for d, a in [("PENNE", 17.97), ("BEEF CUBE", 93.31), ("CASH", 115.00),
                               ("VAT 5%", 5.30)]],
    }  # fmt: skip

    extract_document(session, storage, FakeClient(json.dumps(answer)), doc, max_images=3)

    assert doc.invoice is not None
    assert [line["description"] for line in doc.invoice["lines"]] == ["PENNE", "BEEF CUBE"]
    assert (doc.invoice["subtotal"], doc.invoice["vat"], doc.invoice["total"]) == (
        "105.98", "5.30", "111.28"
    )  # fmt: skip
    assert doc.invoice["amount_type"] == "INCLUSIVE"
    [row] = extractions(session, doc)
    assert "CASH" in row.raw_output  # the model's own answer is kept
