"""Read (architecture §4, step 3): every page becomes an image plus text with positions.

- Digital PDF page: the text layer (PyMuPDF), exact and fast.
- Scanned page or photo: OCR on the page image. Photos are turned upright from their EXIF
  orientation first (phone photos of receipts are often sideways).
Per page, Storage gets a PNG (for the model and the Workbench viewer) and a JSON with text spans
and boxes as fractions of the page (0-1), so the Workbench can highlight at any zoom.
"""

import json
import logging
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Literal

import cv2
import numpy as np
import pymupdf
from sqlalchemy.orm import Session

from app.adapters.ocr import Image, OcrEngine
from app.adapters.storage import Storage
from app.adapters.storage.keys import ocr_key, page_image_key
from app.domain.enums import DocumentStatus
from app.domain.state_machine import transition
from app.models import Document

logger = logging.getLogger(__name__)

ACTOR = "system:read"


@dataclass(frozen=True, slots=True)
class TextSpan:
    text: str
    box: tuple[float, float, float, float]  # x0, y0, x1, y1 as fractions of the page
    confidence: float | None  # None for the text layer (exact)


@dataclass(slots=True)
class PageText:
    page: int  # 1-based
    width: int  # page image size in pixels
    height: int
    source: Literal["text_layer", "ocr"]
    spans: list[TextSpan] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n".join(span.text for span in self.spans)

    def to_json(self) -> bytes:
        return json.dumps(asdict(self), ensure_ascii=False).encode()

    @classmethod
    def from_json(cls, data: bytes) -> "PageText":
        raw = json.loads(data)
        spans = [TextSpan(s["text"], tuple(s["box"]), s["confidence"]) for s in raw.pop("spans")]
        return cls(**raw, spans=spans)


class ReadOutcome(StrEnum):
    READ = "read"
    FAILED = "failed"
    SKIPPED = "skipped"  # not CAPTURED: already read, or never readable


@dataclass(frozen=True, slots=True)
class ReadResult:
    outcome: ReadOutcome
    pages: int = 0
    ocr_pages: int = 0
    seconds: float = 0.0


def read_document(
    session: Session,
    storage: Storage,
    ocr: Callable[[], OcrEngine],
    document: Document,
    *,
    max_pages: int,
    max_side: int,
    min_text_chars: int,
) -> ReadResult:
    """`ocr` is called only if a page needs OCR, so digital PDFs never load the OCR models."""
    if document.status is not DocumentStatus.CAPTURED:
        return ReadResult(ReadOutcome.SKIPPED)  # safe re-run: nothing to do
    started = time.perf_counter()
    try:
        pages = read_pages(
            storage.get(document.storage_key),
            document.mime_type,
            ocr,
            max_pages=max_pages,
            max_side=max_side,
            min_text_chars=min_text_chars,
        )
    except UnreadableError as exc:
        document.failure_reason = str(exc)
        transition(session, document, DocumentStatus.FAILED, actor=ACTOR, reason=str(exc))
        return ReadResult(ReadOutcome.FAILED)

    for image, text in pages:
        storage.put(page_image_key(document.client_id, document.id, text.page), _png(image))
        storage.put(ocr_key(document.client_id, document.id, text.page), text.to_json())

    ocr_pages = sum(text.source == "ocr" for _, text in pages)
    document.page_count = len(pages)
    transition(
        session,
        document,
        DocumentStatus.READ,
        actor=ACTOR,
        details={"pages": len(pages), "ocr_pages": ocr_pages},
    )
    return ReadResult(ReadOutcome.READ, len(pages), ocr_pages, time.perf_counter() - started)


def read_pages(
    data: bytes,
    mime_type: str,
    ocr: Callable[[], OcrEngine],
    *,
    max_pages: int,
    max_side: int,
    min_text_chars: int,
) -> list[tuple[Image, PageText]]:
    """File bytes -> (page image, page text) per page. Raises UnreadableError."""
    result = []
    for number, (image, page) in enumerate(
        _render_pages(data, mime_type, max_pages=max_pages, max_side=max_side), start=1
    ):
        text = _text_layer(page, image, number, min_text_chars) if page is not None else None
        result.append((image, text or _ocr(ocr(), image, number)))
    return result


def load_page_texts(storage: Storage, document: Document) -> list[PageText]:
    """Text of every page, as saved by `read_document` (used by Extract)."""
    return [
        PageText.from_json(storage.get(ocr_key(document.client_id, document.id, number)))
        for number in range(1, (document.page_count or 0) + 1)
    ]


class UnreadableError(Exception):
    """The file cannot be read (damaged, encrypted, too many pages). The document FAILs."""


def _render_pages(
    data: bytes, mime_type: str, *, max_pages: int, max_side: int
) -> list[tuple[Image, pymupdf.Page | None]]:
    """(page image, PDF page or None for photos). PDF pages keep their page for the text layer."""
    if mime_type != "application/pdf":
        return [(_photo(data, max_side), None)]
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except (pymupdf.FileDataError, RuntimeError) as exc:
        raise UnreadableError("cannot open PDF (damaged or encrypted)") from exc
    if doc.needs_pass:
        raise UnreadableError("PDF is password-protected")
    if doc.page_count == 0:
        raise UnreadableError("PDF has no pages")
    if doc.page_count > max_pages:
        raise UnreadableError(f"too many pages ({doc.page_count}; limit {max_pages})")
    rendered = []
    for index in range(doc.page_count):
        page = doc.load_page(index)
        zoom = max_side / max(page.rect.width, page.rect.height)
        pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
        rgb = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3)
        rendered.append((np.ascontiguousarray(rgb[:, :, ::-1]), page))  # RGB -> BGR
    return rendered


def _photo(data: bytes, max_side: int) -> Image:
    # IMREAD_COLOR applies the EXIF orientation, so sideways phone photos come out upright.
    image = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise UnreadableError("cannot decode image (damaged file)")
    height, width = image.shape[:2]
    scale = max_side / max(height, width)
    if scale < 1:  # shrink large photos; never enlarge small ones
        size = (round(width * scale), round(height * scale))
        image = cv2.resize(image, size, interpolation=cv2.INTER_AREA)
    return np.asarray(image, dtype=np.uint8)


def _text_layer(page: pymupdf.Page, image: Image, number: int, min_chars: int) -> PageText | None:
    """The PDF's own text if the page has enough of it; None = scanned page, needs OCR."""
    words = page.get_text("words")
    if sum(len(w[4].strip()) for w in words) < min_chars:
        return None
    width, height = page.rect.width, page.rect.height
    spans = [
        TextSpan(w[4], _fractions((w[0], w[1], w[2], w[3]), width, height), None) for w in words
    ]
    return PageText(number, image.shape[1], image.shape[0], "text_layer", spans)


def _ocr(engine: OcrEngine, image: Image, number: int) -> PageText:
    height, width = image.shape[:2]
    spans = [
        TextSpan(r.text, _fractions(r.box, width, height), round(r.confidence, 4))
        for r in engine.recognize(image)
        if r.text.strip()
    ]
    return PageText(number, width, height, "ocr", spans)


def _fractions(
    box: tuple[float, float, float, float], width: float, height: float
) -> tuple[float, float, float, float]:
    def clamp(value: float) -> float:
        return round(min(max(value, 0.0), 1.0), 5)

    x0, y0, x1, y1 = box
    return (clamp(x0 / width), clamp(y0 / height), clamp(x1 / width), clamp(y1 / height))


def _png(image: Image) -> bytes:
    ok, encoded = cv2.imencode(".png", image)
    if not ok:
        raise RuntimeError("PNG encoding failed")
    return encoded.tobytes()
