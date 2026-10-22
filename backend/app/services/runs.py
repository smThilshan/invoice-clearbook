"""Runs: start one (a folder the user picked inside a client's folder, decision 25) and report
its progress.

Progress is computed live from the documents, per file the scan listed, so it can never drift
from the truth. A file that was already processed in an earlier run (Run clicked twice) shows
that earlier document instead of looking stuck.
"""

import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Final, Literal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain.enums import Direction, DocumentStatus, DocumentType
from app.models import Client, Document, Run, RunStatus
from app.pipeline.folders import InboxError, folder_date_from_path, relative_to_root, resolve_inside
from app.services.clients import client_root
from app.services.timings import StepTimings, load_timings

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
FROM_FOLDER: Final[Literal["from_folder"]] = (
    "from_folder"  # start_run: read the date from the folder name
)


class RunState(StrEnum):
    SCANNING = "scanning"  # folder not listed yet
    PROCESSING = "processing"  # some files are queued or between steps
    FINISHED = "finished"  # every file settled: in review, parked, failed or duplicate


@dataclass(frozen=True, slots=True)
class FileProgress:
    path: str  # relative to the client's folder
    document_id: uuid.UUID | None  # None = not ingested yet
    status: DocumentStatus | None
    document_type: DocumentType | None
    page_count: int | None
    failure_reason: str | None
    from_earlier_run: bool  # already processed before; this run found it again
    timings: StepTimings | None = None  # None = not ingested yet

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

    @property
    def status(self) -> RunStatus:
        """Derived, never stored twice: FAILED is set when the run could not proceed;
        otherwise COMPLETED once every file has settled."""
        if self.run.status is RunStatus.FAILED:
            return RunStatus.FAILED
        return RunStatus.COMPLETED if self.state is RunState.FINISHED else RunStatus.RUNNING


def start_run(
    session: Session,
    *,
    client: Client,
    folder: str = "",
    folder_date: date | Literal["from_folder"] | None = FROM_FOLDER,
    today: date,
    created_by: str | None = None,
) -> Run:
    """Create the run row for `folder` (relative to the client's folder; "" = all of it).
    The date is the user's (None: they confirmed there is none), by default read from the
    folder name (a name without a year: the most recent such date up to `today`). The caller
    commits, then enqueues the scan (never before: the worker could start before the row is
    visible)."""
    root = client_root(client)
    path = resolve_inside(root, folder)
    if not path.is_dir():
        raise InboxError(f"folder not found: {folder or '(client folder)'}")
    relative = relative_to_root(root, path) if path != root.resolve() else ""
    run = Run(
        client_id=client.client_id,
        folder=relative,
        direction=Direction.PURCHASE,  # purchase-side by default (decision 25)
        folder_date=(
            folder_date_from_path(relative, today=today)
            if folder_date == FROM_FOLDER
            else folder_date
        ),
        created_by=created_by,
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

    timings = load_timings(session, list(by_path.values()))
    files = [_file_progress(path, by_path.get(path), run.id, timings) for path in paths]
    pending = any(f.status is None or f.status in IN_PROGRESS for f in files)
    return RunProgress(run, RunState.PROCESSING if pending else RunState.FINISHED, files)


def _file_progress(
    path: str,
    document: Document | None,
    run_id: uuid.UUID,
    timings: dict[uuid.UUID, StepTimings],
) -> FileProgress:
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
        timings=timings.get(document.id),
    )


@dataclass(frozen=True, slots=True)
class RunSummary:
    run: Run
    documents: int  # documents this run took in (files already seen belong to earlier runs)


def recent_runs(session: Session, client_id: str, *, limit: int) -> list[RunSummary]:
    """The client's latest runs that took in documents, newest first (queue run filter)."""
    count = func.count(Document.id)
    rows = session.execute(
        select(Run, count)
        .join(Document, Document.run_id == Run.id)
        .where(Run.client_id == client_id, Document.client_id == client_id)
        .group_by(Run.id)
        .order_by(Run.created_at.desc())
        .limit(limit)
    )
    return [RunSummary(run, documents) for run, documents in rows]
