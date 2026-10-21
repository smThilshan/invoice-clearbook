"""Run + Ingest (architecture §4, steps 1-2).

A run lists the files in the folder the user picked inside a client's folder, sub-folders
included (decision 25); each file is ingested on its own: stable-file check, SHA-256
(duplicate check 1), type/size check, copy into Storage, and one `documents` row. The client's
folder belongs to the accountants: it is only read, never changed. Paths are relative to it.

Outcomes per file:
- created       new document, CAPTURED
- duplicate     same content as an earlier document under another name/path: DUPLICATE
- failed        unsupported, empty or too large: document FAILED with the reason (never dropped)
- already_seen  this exact file was ingested before (Run clicked twice): nothing to do
- not_ready     still being copied into the inbox: try again later
"""

import hashlib
import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.adapters.storage import Storage
from app.adapters.storage.keys import original_key
from app.domain.enums import DocumentStatus
from app.domain.state_machine import transition
from app.models import Document, Run
from app.pipeline import file_types
from app.pipeline.folders import InboxError, list_files, relative_to_root, resolve_inside

logger = logging.getLogger(__name__)

ACTOR = "system:ingest"

__all__ = ["InboxError", "IngestResult", "Outcome", "ingest_file", "scan_run"]

_HASH_CHUNK = 1024 * 1024
_UNKNOWN = file_types.FileType("application/octet-stream", ".bin")


class Outcome(StrEnum):
    CREATED = "created"
    DUPLICATE = "duplicate"
    FAILED = "failed"
    ALREADY_SEEN = "already_seen"
    NOT_READY = "not_ready"


@dataclass(frozen=True, slots=True)
class IngestResult:
    outcome: Outcome
    document_id: uuid.UUID | None = None


def scan_run(session: Session, run: Run, client_root: Path) -> list[str]:
    """List the run's files (sub-folders included) and record them on the run.
    Returns paths relative to the client's folder."""
    folder = resolve_inside(client_root, run.folder)
    if not folder.is_dir():
        raise InboxError(f"folder not found: {run.folder or '(client folder)'}")
    paths = [relative_to_root(client_root, path) for path in list_files(folder)]
    run.files = paths  # what the accountants dropped; run progress is tracked per file
    run.files_found = len(paths)
    return paths


def ingest_file(
    session: Session,
    storage: Storage,
    *,
    run: Run,
    client_root: Path,
    relative_path: str,
    max_bytes: int,
    stable_seconds: float,
    sleep: Callable[[float], None] = time.sleep,
) -> IngestResult:
    path = resolve_inside(client_root, relative_path)
    if not path.is_file():
        raise InboxError(f"not a file in the client folder: {relative_path!r}")

    before = path.stat()
    sleep(stable_seconds)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        return IngestResult(Outcome.NOT_READY)

    sha256, head = _fingerprint(path)
    seen = _classify_existing(session, run, sha256, relative_path)
    if seen is not None:
        return seen

    file_type = file_types.detect(head)
    problem = _problem(after.st_size, max_bytes, file_type)
    key = original_key(run.client_id, sha256, (file_type or _UNKNOWN).extension)
    if not storage.exists(key):  # content-addressed: an identical file is stored once
        storage.put_file(key, path)

    document = Document(
        client_id=run.client_id,
        run_id=run.id,
        direction=run.direction,
        folder_date=run.folder_date,
        source_path=relative_path,
        storage_key=key,
        sha256=sha256,
        mime_type=(file_type or _UNKNOWN).mime_type,
        size_bytes=after.st_size,
        status=DocumentStatus.CAPTURED,
    )
    try:
        with session.begin_nested():
            session.add(document)
    except IntegrityError:
        # Another worker ingested the same content between our check and insert.
        session.expunge(document)
        return _classify_existing(session, run, sha256, relative_path) or (
            IngestResult(Outcome.ALREADY_SEEN)
        )

    if problem is not None:
        document.failure_reason = problem
        transition(session, document, DocumentStatus.FAILED, actor=ACTOR, reason=problem)
        return IngestResult(Outcome.FAILED, document.id)
    return IngestResult(Outcome.CREATED, document.id)


def _classify_existing(
    session: Session, run: Run, sha256: str, relative_path: str
) -> IngestResult | None:
    """None = content never seen. Otherwise: already seen (same path) or a new DUPLICATE,
    which belongs to the run (and folder) where it was found, not the original's."""
    existing = session.scalars(
        select(Document)
        .where(Document.client_id == run.client_id, Document.sha256 == sha256)
        .order_by(Document.created_at)
    ).all()
    if not existing:
        return None
    if any(doc.source_path == relative_path for doc in existing):
        return IngestResult(Outcome.ALREADY_SEEN)

    original = next(
        (doc for doc in existing if doc.status is not DocumentStatus.DUPLICATE), existing[0]
    )
    duplicate = Document(
        client_id=run.client_id,
        run_id=run.id,
        direction=run.direction,
        folder_date=run.folder_date,
        source_path=relative_path,
        storage_key=original.storage_key,
        sha256=sha256,
        mime_type=original.mime_type,
        size_bytes=original.size_bytes,
        # Created directly as DUPLICATE: the unique index allows one live copy per content.
        status=DocumentStatus.DUPLICATE,
        failure_reason=f"same file as document {original.id} ({original.source_path})",
    )
    session.add(duplicate)
    session.flush()
    return IngestResult(Outcome.DUPLICATE, duplicate.id)


def _fingerprint(path: Path) -> tuple[str, bytes]:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        head = stream.read(file_types.HEAD_BYTES)
        digest.update(head)
        while chunk := stream.read(_HASH_CHUNK):
            digest.update(chunk)
    return digest.hexdigest(), head


def _problem(size: int, max_bytes: int, file_type: file_types.FileType | None) -> str | None:
    if size == 0:
        return "empty file"
    if size > max_bytes:
        return f"file too large ({size / 1_048_576:.1f} MB; limit {max_bytes // 1_048_576} MB)"
    if file_type is None:
        return f"unsupported file type (supported: {file_types.SUPPORTED_DESCRIPTION})"
    return None
