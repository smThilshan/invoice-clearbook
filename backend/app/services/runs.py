"""Runs: start one (a typed inbox folder for one date) and report its progress.

Progress is computed live from the documents, per file the scan listed, so it can never drift
from the truth. A file that was already processed in an earlier run (Run clicked twice) shows
that earlier document instead of looking stuck.
"""

import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.enums import Direction, DocumentStatus, DocumentType
from app.models import Document, Run
from app.pipeline.ingest import InboxError, run_folder

# Statuses that will move on by themselves (a pipeline step is pending or running).
IN_PROGRESS = frozenset(
    {
        DocumentStatus.CAPTURED,
        DocumentStatus.READ,
        DocumentStatus.EXTRACTED,
        DocumentStatus.VERIFIED,
    }
)
QUEUED = "QUEUED"  # listed by the scan, not ingested yet


class RunState(StrEnum):
    SCANNING = "scanning"  # folder not listed yet
    PROCESSING = "processing"  # some files are queued or between steps
    FINISHED = "finished"  # every file settled: in review, parked, failed or duplicate


@dataclass(frozen=True, slots=True)
class FileProgress:
    path: str  # inbox-relative
    document_id: uuid.UUID | None  # None = not ingested yet
    status: DocumentStatus | None
    document_type: DocumentType | None
    page_count: int | None
    failure_reason: str | None
    from_earlier_run: bool  # already processed before; this run found it again

    @property
    def status_label(self) -> str:
        return self.status.value if self.status else QUEUED


@dataclass(frozen=True, slots=True)
class RunProgress:
    run: Run
    state: RunState
    files: list[FileProgress]

    @property
    def counts(self) -> dict[str, int]:
        return dict(Counter(file.status_label for file in self.files))


def start_run(
    session: Session,
    *,
    client_id: str,
    direction: Direction,
    folder_date: date,
    inbox_dir: Path,
    created_by: str | None = None,
) -> Run:
    """Create the run row. The caller commits, then enqueues the scan (never before commit:
    the worker could start before the row is visible)."""
    if not run_folder(inbox_dir, direction, folder_date).is_dir():
        raise InboxError(f"no inbox folder for {direction.value.title()} on {folder_date}")
    run = Run(
        client_id=client_id, direction=direction, folder_date=folder_date, created_by=created_by
    )
    session.add(run)
    session.flush()
    return run


def run_progress(session: Session, run: Run) -> RunProgress:
    if run.files is None:
        return RunProgress(run, RunState.SCANNING, [])

    paths = [str(path) for path in run.files]
    by_path: dict[str, Document] = {}
    for document in session.scalars(
        select(Document)
        .where(Document.client_id == run.client_id, Document.source_path.in_(paths))
        .order_by(Document.created_at)
    ):
        current = by_path.get(document.source_path)
        # This run's own document wins; otherwise the most recent one for that path.
        if current is None or current.run_id != run.id:
            by_path[document.source_path] = document

    files = [_file_progress(path, by_path.get(path), run.id) for path in paths]
    pending = any(f.status is None or f.status in IN_PROGRESS for f in files)
    return RunProgress(run, RunState.PROCESSING if pending else RunState.FINISHED, files)


def _file_progress(path: str, document: Document | None, run_id: uuid.UUID) -> FileProgress:
    if document is None:
        return FileProgress(path, None, None, None, None, None, from_earlier_run=False)
    return FileProgress(
        path,
        document.id,
        document.status,
        document.document_type,
        document.page_count,
        document.failure_reason,
        from_earlier_run=document.run_id != run_id,
    )
