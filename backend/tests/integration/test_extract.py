import json
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.llm import ExtractionError, ExtractionRequest, ExtractionResult
from app.adapters.storage.local import LocalStorage
from app.domain.enums import DocumentStatus, DocumentType
from app.models import AuditLog, Document, Extraction
from app.pipeline.extract import PROMPT_VERSION, ExtractOutcome, extract_document
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

    def extract(self, request: ExtractionRequest) -> ExtractionResult:
        self.requests.append(request)
        if self.fail:
            raise ExtractionError("model timed out after 600s")
        return ExtractionResult(self.model, self.raw, False, 1234, 2000, 150, 1000, 200)

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
