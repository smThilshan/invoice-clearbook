"""Documents in review (task 4.2): queue, one document with its pages, edit, reject.

No login yet (decision 26): the API listens on localhost only. Responses carry invoice values
for the Workbench; they are never logged.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from pathlib import PurePosixPath
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.adapters.storage import Storage, get_storage
from app.adapters.storage.base import NotFoundError
from app.adapters.storage.keys import ocr_key, page_image_key
from app.core.config import Settings, get_settings
from app.core.db import get_session
from app.domain.enums import DocumentStatus, DocumentType, QueueCategory
from app.domain.invoice import Invoice
from app.domain.state_machine import StaleStatusError
from app.models import Document
from app.pipeline.read import PageText, load_page_texts
from app.pipeline.verify.locate import ValueKind, locate
from app.pipeline.verify.step import working_copy
from app.services import review
from app.services.clients import get_client
from app.services.review import RejectReason, ReviewError

router = APIRouter(prefix="/documents", tags=["documents"])


class IssueOut(BaseModel):
    code: str
    severity: Literal["ERROR", "WARNING"]
    field: str | None
    message: str
    suggestion: str | None = None  # the value printed on the document, to offer in review


class DocumentSummary(BaseModel):
    document_id: uuid.UUID
    client_id: str
    run_id: uuid.UUID
    file: str  # file name; the full path is in the detail
    status: DocumentStatus
    queue: QueueCategory | None
    document_type: DocumentType | None
    vendor_name: str | None
    invoice_number: str | None
    invoice_date: date | None
    total: Decimal | None
    currency: str | None
    errors: int
    warnings: int
    created_at: datetime


class QueuePageOut(BaseModel):
    total: int
    limit: int
    offset: int
    documents: list[DocumentSummary]


class DocumentDetail(DocumentSummary):
    source_path: str  # inside the client's folder
    mime_type: str
    folder_date: date | None
    page_count: int | None
    invoice: Invoice | None  # None: the extraction could not be read, enter it by hand
    issues: list[IssueOut]
    coding: list[dict[str, Any]]  # per line: where the suggested codes came from
    verified_fields: list[str]
    contact_id: uuid.UUID | None
    failure_reason: str | None
    updated_at: datetime


class DocumentUpdate(BaseModel):
    invoice: Invoice
    confirmed: list[str] = Field(
        default_factory=list,
        description="Fields checked against the original and left as they are",
    )


class RejectBody(BaseModel):
    reason: RejectReason
    note: str | None = Field(default=None, max_length=500)


class Span(BaseModel):
    text: str
    box: tuple[float, float, float, float]  # x0, y0, x1, y1 as fractions of the page
    confidence: float | None


class PageTextOut(BaseModel):
    page: int
    width: int
    height: int
    source: Literal["text_layer", "ocr"]
    spans: list[Span]


class Place(BaseModel):
    page: int
    box: tuple[float, float, float, float]  # x0, y0, x1, y1 as fractions of the page
    exact: bool  # False: most words of a name found, not all (drawn as approximate)


class Located(BaseModel):
    places: list[Place]  # empty: not printed on the document (or not a valid value)


def get_review_storage(settings: Annotated[Settings, Depends(get_settings)]) -> Storage:
    return get_storage(settings)


SessionDep = Annotated[Session, Depends(get_session)]
StorageDep = Annotated[Storage, Depends(get_review_storage)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


@router.get("")
def get_queue(
    session: SessionDep,
    client_id: str,
    queue: QueueCategory | None = None,
    run_id: uuid.UUID | None = None,
    status_: Annotated[DocumentStatus, Query(alias="status")] = DocumentStatus.IN_REVIEW,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> QueuePageOut:
    """Exception first, then Check, then Ready; oldest first inside each."""
    if get_client(session, client_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "client not found")
    page = review.list_queue(session, client_id=client_id, status=status_, queue=queue,
                             run_id=run_id, limit=limit, offset=offset)  # fmt: skip
    return QueuePageOut(
        total=page.total,
        limit=limit,
        offset=offset,
        documents=[_summary(document) for document in page.documents],
    )


@router.get("/{document_id}")
def get_document(document_id: uuid.UUID, session: SessionDep) -> DocumentDetail:
    return _detail(_found(session, document_id))


@router.get(
    "/{document_id}/pages/{page}/image",
    response_class=Response,
    responses={200: {"content": {"image/png": {}}}},
)
def get_page_image(
    document_id: uuid.UUID, page: int, session: SessionDep, storage: StorageDep
) -> Response:
    document = _found(session, document_id)
    _check_page(document, page)
    try:
        data = storage.get(page_image_key(document.client_id, document.id, page))
    except NotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "page image not found") from exc
    return Response(data, media_type="image/png", headers={"Cache-Control": "private, no-cache"})


@router.get("/{document_id}/pages/{page}/text")
def get_page_text(
    document_id: uuid.UUID, page: int, session: SessionDep, storage: StorageDep
) -> PageTextOut:
    """Text spans with their boxes, for highlighting a value on the page image."""
    document = _found(session, document_id)
    _check_page(document, page)
    try:
        text = PageText.from_json(storage.get(ocr_key(document.client_id, document.id, page)))
    except NotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "page text not found") from exc
    return PageTextOut(
        page=text.page,
        width=text.width,
        height=text.height,
        source=text.source,
        spans=[Span(text=s.text, box=s.box, confidence=s.confidence) for s in text.spans],
    )


@router.get("/{document_id}/locate")
def locate_value(
    document_id: uuid.UUID,
    session: SessionDep,
    storage: StorageDep,
    kind: ValueKind,
    value: Annotated[str, Query(max_length=200)],
) -> Located:
    """Where `value` is printed on the document, for highlighting a field (task 4.6). The value
    may be what the accountant is typing, so it is compared the way grounding compares."""
    document = _found(session, document_id)
    try:
        pages = load_page_texts(storage, document)
    except NotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "page text not found") from exc
    found = locate(pages, kind, value)
    return Located(places=[Place(page=m.page, box=m.box, exact=m.exact) for m in found])


@router.patch("/{document_id}")
def update_document(
    document_id: uuid.UUID,
    body: DocumentUpdate,
    session: SessionDep,
    storage: StorageDep,
    settings: SettingsDep,
) -> DocumentDetail:
    """Save the corrected invoice; every check runs again (no model call)."""
    document = _found(session, document_id, for_update=True)
    try:
        with session.begin_nested():  # an error undoes only this request's changes
            review.update_document(session, storage, document, body.invoice,
                                   confirmed=body.confirmed, settings=settings,
                                   today=settings.business_today())  # fmt: skip
    except (ReviewError, StaleStatusError) as exc:
        raise _http_error(exc) from exc
    session.commit()
    return _detail(document)


@router.post("/{document_id}/reject")
def reject_document(
    document_id: uuid.UUID, body: RejectBody, session: SessionDep
) -> DocumentDetail:
    document = _found(session, document_id, for_update=True)
    try:
        with session.begin_nested():
            review.reject_document(session, document, body.reason, note=body.note)
    except (ReviewError, StaleStatusError) as exc:
        raise _http_error(exc) from exc
    session.commit()
    return _detail(document)


def _found(session: Session, document_id: uuid.UUID, *, for_update: bool = False) -> Document:
    document = review.get_document(session, document_id, for_update=for_update)
    if document is None or get_client(session, document.client_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "document not found")
    return document


def _check_page(document: Document, page: int) -> None:
    if not 1 <= page <= (document.page_count or 0):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "page not found")


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, review.NotEditableError | StaleStatusError):
        return HTTPException(status.HTTP_409_CONFLICT, str(exc))
    return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc))


def _summary(document: Document) -> DocumentSummary:
    return DocumentSummary(**_summary_fields(document, working_copy(document)))


def _summary_fields(document: Document, invoice: Invoice | None) -> dict[str, Any]:
    errors, warnings = review.issue_counts(document)
    return {
        "document_id": document.id,
        "client_id": document.client_id,
        "run_id": document.run_id,
        "file": PurePosixPath(document.source_path).name,
        "status": document.status,
        "queue": document.queue,
        "document_type": document.document_type,
        "vendor_name": invoice.vendor_name if invoice else None,
        "invoice_number": invoice.invoice_number if invoice else None,
        "invoice_date": invoice.invoice_date if invoice else None,
        "total": invoice.total if invoice else None,
        "currency": invoice.currency if invoice else None,
        "errors": errors,
        "warnings": warnings,
        "created_at": document.created_at,
    }


def _detail(document: Document) -> DocumentDetail:
    invoice = working_copy(document)
    return DocumentDetail(
        **_summary_fields(document, invoice),
        source_path=document.source_path,
        mime_type=document.mime_type,
        folder_date=document.folder_date,
        page_count=document.page_count,
        invoice=invoice,
        issues=[IssueOut(**issue) for issue in document.issues or []],
        coding=document.coding or [],
        verified_fields=document.verified_fields or [],
        contact_id=document.contact_id,
        failure_reason=document.failure_reason,
        updated_at=document.updated_at,
    )
