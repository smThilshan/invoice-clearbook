"""Runs: start one (a folder the user picked inside a client's folder, decision 25) and report
its progress.

Progress is computed live from the documents, per file the scan listed, so it can never drift
from the truth. A file that was already processed in an earlier run (Run clicked twice) shows
that earlier document instead of looking stuck.
"""

import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from pathlib import Path
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


class Step(StrEnum):
    """Where a file is right now, for the live progress page (task 4.14)."""

    QUEUED = "queued"  # listed by the scan, not taken in yet
    WAITING = "waiting"  # taken in, its next step is queued
    READING = "reading"  # page text and pictures being made
    WAITING_AI = "waiting_ai"  # in line for the AI model
    AI = "ai"  # the AI model is reading it
    CHECKS = "checks"  # coding and checks running
    DONE = "done"  # in review, parked, duplicate or further
    FAILED = "failed"
    STOPPED = "stopped"  # its run was stopped before this file was done (task 4.19)


# The marker each worker step sets (tasks._mark_working), and the status it works on.
_ACTIVE = {
    DocumentStatus.CAPTURED: ("read", Step.READING),
    DocumentStatus.READ: ("extract", Step.AI),
    DocumentStatus.EXTRACTED: ("verify", Step.CHECKS),
    DocumentStatus.VERIFIED: ("verify", Step.CHECKS),
}
TYPICAL_SAMPLE = 20  # recent documents the typical time per file is taken from
MARK_EXPIRES = timedelta(minutes=35)  # longer than the job time limit (30 min)


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
    step: Step = Step.QUEUED
    step_since: datetime | None = None  # when the current step started (working steps only)
    queue_position: int | None = None  # waiting for the AI: 1 = next

    @property
    def status_label(self) -> str:
        return self.status.value if self.status else QUEUED


@dataclass(frozen=True, slots=True)
class RunProgress:
    run: Run
    state: RunState
    files: list[FileProgress]
    typical_ms: int | None = None  # recent work time per file, for "time left"

    @property
    def counts(self) -> dict[str, int]:
        return dict(Counter(file.status_label for file in self.files))

    @property
    def status(self) -> RunStatus:
        """Derived, never stored twice: FAILED is set when the run could not proceed;
        otherwise COMPLETED once every file has settled."""
        if self.run.status in (RunStatus.FAILED, RunStatus.STOPPED):
            return self.run.status
        return RunStatus.COMPLETED if self.state is RunState.FINISHED else RunStatus.RUNNING


def start_run(
    session: Session,
    *,
    client: Client,
    root: Path | None = None,
    folder: str = "",
    folder_date: date | Literal["from_folder"] | None = FROM_FOLDER,
    today: date,
    created_by: str | None = None,
) -> Run:
    """Create the run row for `folder`, relative to `root`: the place it was picked in
    (decision 35; None = the client's own folder). "" = the whole place.
    The date is the user's (None: they confirmed there is none), by default read from the
    folder name (a name without a year: the most recent such date up to `today`). The caller
    commits, then enqueues the scan (never before: the worker could start before the row is
    visible)."""
    root = root or client_root(client)
    path = resolve_inside(root, folder)
    if not path.is_dir():
        raise InboxError(f"folder not found: {folder or str(root)}")
    relative = relative_to_root(root, path) if path != root.resolve() else ""
    run = Run(
        client_id=client.client_id,
        root_path=str(root),
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
    line = _ai_line(session)
    stopped = run.status is RunStatus.STOPPED
    files = [
        _file_progress(path, by_path.get(path), run.id, timings, line, stopped) for path in paths
    ]
    pending = any(f.status is None or f.status in IN_PROGRESS for f in files)
    state = RunState.PROCESSING if pending else RunState.FINISHED
    return RunProgress(run, state, files, typical_work_ms(session) if pending else None)


def typical_work_ms(session: Session) -> int | None:
    """Median work time of recently processed files (any client): for "time left"."""
    recent = list(
        session.scalars(
            select(Document)
            .where(Document.status.not_in(IN_PROGRESS), Document.page_count.is_not(None))
            .order_by(Document.created_at.desc())
            .limit(TYPICAL_SAMPLE)
        )
    )
    work = sorted(
        t.total_ms - t.waiting_ms
        for t in load_timings(session, recent).values()
        if t.total_ms is not None and t.waiting_ms is not None
    )
    return work[len(work) // 2] if work else None


def _ai_line(session: Session) -> list[uuid.UUID]:
    """Documents waiting for the AI model, oldest first: the worker takes them in this order.
    All clients share the model, so the line is global (only its length is shown)."""
    return list(
        session.scalars(
            select(Document.id)
            .where(Document.status == DocumentStatus.READ)
            .where((Document.working_step.is_(None)) | (Document.working_step != "extract"))
            .order_by(Document.created_at)
        )
    )


def _file_progress(
    path: str,
    document: Document | None,
    run_id: uuid.UUID,
    timings: dict[uuid.UUID, StepTimings],
    ai_line: list[uuid.UUID],
    run_stopped: bool = False,
) -> FileProgress:
    if document is None:
        step = Step.STOPPED if run_stopped else Step.QUEUED
        return FileProgress(path, None, None, None, None, None, from_earlier_run=False, step=step)
    step, since = _step(document)
    if run_stopped and step in (Step.WAITING, Step.WAITING_AI):
        step = Step.STOPPED  # the one still being worked on keeps its step: it finishes first
    waiting = step is Step.WAITING_AI and document.id in ai_line
    position = ai_line.index(document.id) + 1 if waiting else None
    return FileProgress(
        path,
        document.id,
        document.status,
        document.document_type,
        document.page_count,
        document.failure_reason,
        from_earlier_run=document.run_id != run_id,
        timings=timings.get(document.id),
        step=step,
        step_since=since,
        queue_position=position,
    )


def _step(document: Document) -> tuple[Step, datetime | None]:
    if document.status is DocumentStatus.FAILED:
        return Step.FAILED, None
    active = _ACTIVE.get(document.status)
    if active is None:
        return Step.DONE, None
    marker, working = active
    since = document.working_since
    # A mark older than any job may run (the job time limit) is left by a crashed worker.
    live = since is not None and datetime.now(UTC) - since < MARK_EXPIRES
    if document.working_step == marker and live:
        return working, since
    return (Step.WAITING_AI if document.status is DocumentStatus.READ else Step.WAITING), None


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


class RunControlError(Exception):
    """The run cannot be stopped or resumed in its current state."""


@dataclass(frozen=True, slots=True)
class ResumePlan:
    """What a resumed run still needs (task 4.19): its scan, files not taken in, next steps."""

    scan: bool
    ingest: list[str]  # root-relative paths
    steps: list[tuple[uuid.UUID, DocumentStatus]]


def stop_run(run: Run) -> None:
    """Stop a running run (task 4.19): every worker step checks it before starting, so files
    not started stay as they are; a step already running finishes first. The caller commits."""
    if run.status is not RunStatus.RUNNING:
        raise RunControlError(f"only a running run can be stopped (this one is {run.status})")
    run.status = RunStatus.STOPPED


def resume_run(session: Session, run: Run) -> ResumePlan:
    """Continue a stopped run from where each file is. The caller commits, then enqueues."""
    if run.status is not RunStatus.STOPPED:
        raise RunControlError(f"only a stopped run can be resumed (this one is {run.status})")
    run.status = RunStatus.RUNNING
    if run.files is None:
        return ResumePlan(scan=True, ingest=[], steps=[])
    progress = run_progress(session, run)
    return ResumePlan(
        scan=False,
        ingest=[f.path for f in progress.files if f.document_id is None],
        steps=[
            (f.document_id, f.status)
            for f in progress.files
            if f.document_id is not None
            and f.status in IN_PROGRESS
            and not f.from_earlier_run
            and f.step not in (Step.READING, Step.AI, Step.CHECKS)  # already being worked on
        ],
    )
