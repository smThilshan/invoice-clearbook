"""Where each kind of file lives in storage. The only place that builds storage keys.

<client>/originals/<sha256><ext>              the captured file, stored once per content
<client>/pages/<document_id>/page-001.png     rendered page images
<client>/ocr/<document_id>/page-001.json      text + word boxes per page
"""

import re
import uuid

from app.adapters.storage.base import validate_key

_EXTENSION = re.compile(r"\.[a-z0-9]{1,10}")
_SHA256 = re.compile(r"[0-9a-f]{64}")


def original_key(client_id: str, sha256: str, extension: str) -> str:
    """Content-addressed: the same file is stored once, and re-ingesting it is a no-op."""
    extension = extension.lower()
    if not _SHA256.fullmatch(sha256):
        raise ValueError("sha256 must be 64 lowercase hex characters")
    if not _EXTENSION.fullmatch(extension):
        raise ValueError(f"invalid file extension: {extension!r}")
    return validate_key(f"{client_id}/originals/{sha256}{extension}")


def page_image_key(client_id: str, document_id: uuid.UUID, page: int) -> str:
    return validate_key(f"{client_id}/pages/{document_id}/{_page(page)}.png")


def ocr_key(client_id: str, document_id: uuid.UUID, page: int) -> str:
    return validate_key(f"{client_id}/ocr/{document_id}/{_page(page)}.json")


def _page(page: int) -> str:
    if page < 1:
        raise ValueError("pages are numbered from 1")
    return f"page-{page:03d}"
