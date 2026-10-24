"""Rejected documents (decision 45): bring back to review, or delete permanently. Made-up data."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.adapters.storage.keys import ocr_key
from app.adapters.storage.local import LocalStorage
from app.domain.enums import DocumentStatus as S
from app.models import AuditLog, Correction, Deletion, Document, Extraction
from tests.integration.factories import CLIENT, make_document
from tests.integration.test_review_api import (  # noqa: F401  (fixtures)
    api,
    in_review,
    ok,
)
from tests.integration.test_verify import master_data, storage  # noqa: F401

pytestmark = pytest.mark.integration


def reject(api: TestClient, doc: Document) -> None:  # noqa: F811
    ok(api.post(f"/documents/{doc.id}/reject", json={"reason": "WRONG_RESTAURANT"}))


def count(session: Session, model: type, doc: Document) -> int:
    return session.scalars(
        select(func.count()).select_from(model).where(model.document_id == doc.id)  # type: ignore[attr-defined]
    ).one()


def test_a_rejected_document_is_deleted_with_everything_about_it(
    api: TestClient,  # noqa: F811
    session: Session,
    storage: LocalStorage,  # noqa: F811
) -> None:
    doc = in_review(session, storage)
    doc_id, sha, page_text = doc.id, doc.sha256, ocr_key(CLIENT, doc.id, 1)
    reject(api, doc)
    assert storage.exists(page_text)

    response = api.delete(f"/documents/{doc_id}")

    assert response.status_code == 204
    session.expire_all()
    assert session.get(Document, doc_id) is None
    assert (count(session, Extraction, doc), count(session, Correction, doc),
            count(session, AuditLog, doc)) == (0, 0, 0)  # fmt: skip
    assert not storage.exists(page_text)
    proof = session.scalars(select(Deletion).where(Deletion.document_id == doc_id)).one()
    assert (proof.sha256, proof.rejected_reason, proof.client_id) == (
        sha,
        "WRONG_RESTAURANT",
        CLIENT,
    )
    # The same file can come in again, e.g. into the right client's folder.
    make_document(session, sha256=sha)


def test_only_a_rejected_document_can_be_deleted(
    api: TestClient,  # noqa: F811
    session: Session,
    storage: LocalStorage,  # noqa: F811
) -> None:
    doc = in_review(session, storage)

    response = api.delete(f"/documents/{doc.id}")

    assert response.status_code == 409
    assert "only a rejected document" in response.json()["detail"]
    assert session.get(Document, doc.id) is not None


def test_a_document_in_the_accounting_system_is_never_deleted_here(
    api: TestClient,  # noqa: F811
    session: Session,
    storage: LocalStorage,  # noqa: F811
) -> None:
    doc = in_review(session, storage)
    reject(api, doc)
    doc.external_id = "xero-bill-1"
    session.flush()

    response = api.delete(f"/documents/{doc.id}")

    assert response.status_code == 422
    assert "void it there" in response.json()["detail"]


def test_the_original_is_kept_while_a_duplicate_still_points_to_it(
    api: TestClient,  # noqa: F811
    session: Session,
    storage: LocalStorage,  # noqa: F811
) -> None:
    doc = in_review(session, storage)
    storage.put(doc.storage_key, b"%PDF-1.4 made-up")
    make_document(session, status=S.DUPLICATE, sha256=doc.sha256, storage_key=doc.storage_key)
    reject(api, doc)

    api.delete(f"/documents/{doc.id}")

    assert storage.exists(doc.storage_key)


def test_a_rejected_document_comes_back_to_review_checked_again(
    api: TestClient,  # noqa: F811
    session: Session,
    storage: LocalStorage,  # noqa: F811
) -> None:
    doc = in_review(session, storage)
    reject(api, doc)

    body = ok(api.post(f"/documents/{doc.id}/restore"))

    assert body["status"] == "IN_REVIEW"
    last = session.scalars(
        select(AuditLog).where(AuditLog.document_id == doc.id).order_by(AuditLog.id.desc())
    ).first()
    assert last is not None and (last.from_status, last.to_status) == (S.REJECTED, S.IN_REVIEW)


def test_it_cannot_come_back_while_another_document_holds_its_invoice_number(
    api: TestClient,  # noqa: F811
    session: Session,
    storage: LocalStorage,  # noqa: F811
) -> None:
    first = in_review(session, storage, "INV-00501")
    reject(api, first)
    in_review(session, storage, "INV-00501")  # the same supplier and number, now live

    response = api.post(f"/documents/{first.id}/restore")

    assert response.status_code == 422
    assert "same supplier and invoice number" in response.json()["detail"]
    session.refresh(first)
    assert first.status is S.REJECTED
