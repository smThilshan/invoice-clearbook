import hashlib
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.ocr import OcrEngine
from app.adapters.storage.keys import ocr_key, original_key, page_image_key
from app.adapters.storage.local import LocalStorage
from app.domain.enums import DocumentStatus
from app.models import AuditLog, Document
from app.pipeline.read import PageText, ReadOutcome, ReadResult, load_page_texts, read_document
from tests.integration.factories import make_document
from tests.read_fixtures import digital_pdf, scanned_pdf
from tests.test_read_pages import FakeOcr

pytestmark = pytest.mark.integration


@pytest.fixture
def storage(tmp_path: Path) -> LocalStorage:
    return LocalStorage(tmp_path / "store")


def captured(session: Session, storage: LocalStorage, data: bytes, mime: str) -> Document:
    sha = hashlib.sha256(data).hexdigest()
    key = original_key("test-client", sha, ".pdf" if mime == "application/pdf" else ".jpg")
    storage.put(key, data)
    return make_document(session, sha256=sha, storage_key=key, mime_type=mime, size_bytes=len(data))


def run_read(session: Session, storage: LocalStorage, doc: Document, ocr: FakeOcr) -> ReadResult:
    def provider() -> OcrEngine:
        return ocr

    return read_document(
        session, storage, provider, doc, max_pages=5, max_side=1500, min_text_chars=20
    )


def audit(session: Session, doc: Document) -> list[AuditLog]:
    return list(session.scalars(select(AuditLog).where(AuditLog.document_id == doc.id)))


def test_digital_pdf_is_read_without_ocr(session: Session, storage: LocalStorage) -> None:
    doc = captured(session, storage, digital_pdf(pages=2), "application/pdf")
    ocr = FakeOcr()

    result = run_read(session, storage, doc, ocr)

    assert (result.outcome, result.pages, result.ocr_pages) == (ReadOutcome.READ, 2, 0)
    assert ocr.calls == 0
    assert (doc.status, doc.page_count) == (DocumentStatus.READ, 2)
    assert storage.get(page_image_key(doc.client_id, doc.id, 2)).startswith(b"\x89PNG")
    page = PageText.from_json(storage.get(ocr_key(doc.client_id, doc.id, 1)))
    assert page.source == "text_layer" and "INV-00291" in page.text
    [entry] = audit(session, doc)
    work_ms = entry.details.pop("work_ms")  # step timings (4.9)
    assert isinstance(work_ms, int) and work_ms >= 0
    assert (entry.to_status, entry.details) == (DocumentStatus.READ, {"pages": 2, "ocr_pages": 0})


def test_scanned_pdf_is_read_with_ocr(session: Session, storage: LocalStorage) -> None:
    doc = captured(session, storage, scanned_pdf(), "application/pdf")

    result = run_read(session, storage, doc, FakeOcr())

    assert (result.pages, result.ocr_pages) == (1, 1)
    [page] = load_page_texts(storage, doc)
    assert page.source == "ocr" and page.text == "Total AED 1,050.10"


def test_unreadable_file_fails_with_reason(session: Session, storage: LocalStorage) -> None:
    doc = captured(session, storage, b"%PDF-1.7 damaged", "application/pdf")

    result = run_read(session, storage, doc, FakeOcr())

    assert result.outcome is ReadOutcome.FAILED
    assert doc.status is DocumentStatus.FAILED
    assert doc.failure_reason == "cannot open PDF (damaged or encrypted)"
    assert audit(session, doc)[0].reason == doc.failure_reason


def test_rerun_on_read_document_is_skipped(session: Session, storage: LocalStorage) -> None:
    doc = captured(session, storage, digital_pdf(), "application/pdf")
    run_read(session, storage, doc, FakeOcr())

    result = run_read(session, storage, doc, FakeOcr())

    assert result.outcome is ReadOutcome.SKIPPED
    assert len(audit(session, doc)) == 1
    assert session.get_one(Document, doc.id).status is DocumentStatus.READ
