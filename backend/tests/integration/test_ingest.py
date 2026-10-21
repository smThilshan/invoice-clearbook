from collections.abc import Callable
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.storage.local import LocalStorage
from app.domain.enums import Direction, DocumentStatus
from app.models import AuditLog, Document, Run
from app.pipeline.ingest import InboxError, IngestResult, Outcome, ingest_file, scan_run
from tests.integration.factories import make_run

pytestmark = pytest.mark.integration

PDF = b"%PDF-1.7\nfake invoice content\n%%EOF"
FOLDER = "October 2026/20"  # a day folder inside a month folder (decision 25)


@pytest.fixture
def inbox(tmp_path: Path) -> Path:
    folder = tmp_path / "inbox" / FOLDER
    folder.mkdir(parents=True)
    return tmp_path / "inbox"


@pytest.fixture
def storage(tmp_path: Path) -> LocalStorage:
    return LocalStorage(tmp_path / "store")


def drop(inbox: Path, name: str, content: bytes) -> str:
    (inbox / FOLDER / name).write_bytes(content)
    return f"{FOLDER}/{name}"


def ingest(
    session: Session,
    storage: LocalStorage,
    run: Run,
    inbox: Path,
    rel: str,
    *,
    sleep: Callable[[float], None] = lambda _: None,
) -> IngestResult:
    return ingest_file(
        session,
        storage,
        run=run,
        client_root=inbox,
        relative_path=rel,
        max_bytes=1_048_576,
        stable_seconds=0,
        sleep=sleep,
    )


def test_new_file_becomes_captured_document_with_stored_copy(
    session: Session, storage: LocalStorage, inbox: Path
) -> None:
    run = make_run(session)
    rel = drop(inbox, "inv 1.pdf", PDF)

    result = ingest(session, storage, run, inbox, rel)

    assert result.outcome is Outcome.CREATED
    doc = session.get_one(Document, result.document_id)
    assert doc.status is DocumentStatus.CAPTURED
    assert (doc.source_path, doc.mime_type, doc.size_bytes) == (rel, "application/pdf", len(PDF))
    assert doc.storage_key == f"{run.client_id}/originals/{doc.sha256}.pdf"
    assert storage.get(doc.storage_key) == PDF
    assert (inbox / rel).exists()  # the inbox is never changed


def test_same_file_again_is_already_seen(
    session: Session, storage: LocalStorage, inbox: Path
) -> None:
    run = make_run(session)
    rel = drop(inbox, "inv.pdf", PDF)
    ingest(session, storage, run, inbox, rel)

    result = ingest(session, storage, run, inbox, rel)

    assert result == IngestResult(Outcome.ALREADY_SEEN)
    assert len(session.scalars(select(Document)).all()) == 1


def test_same_content_under_another_name_is_a_duplicate(
    session: Session, storage: LocalStorage, inbox: Path
) -> None:
    run = make_run(session)
    original = ingest(session, storage, run, inbox, drop(inbox, "inv.pdf", PDF))

    result = ingest(session, storage, run, inbox, drop(inbox, "inv copy.pdf", PDF))

    assert result.outcome is Outcome.DUPLICATE
    duplicate = session.get_one(Document, result.document_id)
    assert duplicate.status is DocumentStatus.DUPLICATE
    assert str(original.document_id) in (duplicate.failure_reason or "")
    assert duplicate.storage_key == session.get_one(Document, original.document_id).storage_key


def test_duplicate_belongs_to_the_run_and_folder_where_it_was_found(
    session: Session, storage: LocalStorage, inbox: Path
) -> None:
    first_run = make_run(session)
    ingest(session, storage, first_run, inbox, drop(inbox, "inv.pdf", PDF))
    later_run = Run(
        client_id=first_run.client_id,
        direction=Direction.UNSORTED,
        folder_date=date(2026, 10, 21),
    )
    session.add(later_run)
    session.flush()
    later_folder = inbox / "Unsorted/2026-10-21"
    later_folder.mkdir(parents=True)
    (later_folder / "resent.pdf").write_bytes(PDF)

    result = ingest(session, storage, later_run, inbox, "Unsorted/2026-10-21/resent.pdf")

    duplicate = session.get_one(Document, result.document_id)
    assert result.outcome is Outcome.DUPLICATE
    assert (duplicate.run_id, duplicate.direction, duplicate.folder_date) == (
        later_run.id,
        Direction.UNSORTED,
        date(2026, 10, 21),
    )


@pytest.mark.parametrize(
    ("content", "reason"),
    [
        (b"PK\x03\x04 a zip file", "unsupported file type"),
        (b"", "empty file"),
        (PDF + b"x" * 2_000_000, "file too large"),
    ],
)
def test_bad_files_become_failed_documents_with_reason(
    session: Session, storage: LocalStorage, inbox: Path, content: bytes, reason: str
) -> None:
    run = make_run(session)

    result = ingest(session, storage, run, inbox, drop(inbox, "bad.pdf", content))

    assert result.outcome is Outcome.FAILED
    doc = session.get_one(Document, result.document_id)
    assert doc.status is DocumentStatus.FAILED
    assert (doc.failure_reason or "").startswith(reason)
    [audit] = session.scalars(select(AuditLog).where(AuditLog.document_id == doc.id)).all()
    assert (audit.from_status, audit.to_status) == (DocumentStatus.CAPTURED, DocumentStatus.FAILED)


def test_file_still_being_copied_is_not_ready(
    session: Session, storage: LocalStorage, inbox: Path
) -> None:
    run = make_run(session)
    rel = drop(inbox, "growing.pdf", PDF)

    def copy_continues(_: float) -> None:
        with (inbox / rel).open("ab") as stream:
            stream.write(b"more bytes")

    result = ingest(session, storage, run, inbox, rel, sleep=copy_continues)

    assert result == IngestResult(Outcome.NOT_READY)
    assert session.scalars(select(Document)).all() == []


def test_path_outside_inbox_rejected(session: Session, storage: LocalStorage, inbox: Path) -> None:
    run = make_run(session)

    with pytest.raises(InboxError):
        ingest(session, storage, run, inbox, "../../etc/hosts")


def test_scan_lists_files_in_the_picked_folder_and_its_subfolders(
    session: Session, inbox: Path
) -> None:
    run = make_run(session)
    run.folder = "October 2026"  # the whole month
    drop(inbox, "b.pdf", PDF)
    drop(inbox, ".DS_Store", b"")
    (inbox / "October 2026" / "21").mkdir()
    (inbox / "October 2026" / "21" / "a.jpg").write_bytes(b"\xff\xd8\xff")
    (inbox / "Other month.pdf").write_bytes(PDF)  # outside the picked folder

    paths = scan_run(session, run, inbox)

    assert paths == ["October 2026/20/b.pdf", "October 2026/21/a.jpg"]
    assert run.files_found == 2


def test_scan_missing_folder_raises(session: Session, inbox: Path) -> None:
    run = make_run(session)
    run.folder = "November 2026"

    with pytest.raises(InboxError, match="not found"):
        scan_run(session, run, inbox)
