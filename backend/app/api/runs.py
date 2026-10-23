"""Runs: process a folder the user picked in an allowed place (decisions 25, 35).

No login yet (task 4.1): the API listens on localhost only.
"""

import uuid
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import PurePosixPath
from typing import Annotated, Protocol

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.db import get_session
from app.domain.enums import DocumentStatus, DocumentType
from app.models import Run, RunStatus
from app.pipeline.folders import InboxError
from app.services.clients import get_client
from app.services.places import find_place
from app.services.runs import (
    FROM_FOLDER,
    RunControlError,
    RunState,
    Step,
    force_stop_run,
    resume_run,
    run_progress,
    start_run,
    stop_run,
    typical_work_ms,
)
from app.services.timings import StepTimings

router = APIRouter(prefix="/runs", tags=["runs"])


class RunCreate(BaseModel):
    client_id: str
    place: str | None = Field(
        default=None,
        description="Where the folder was picked (GET /folders/places); left out: the "
        "client's own folder (decision 35)",
    )
    folder: str = Field(default="", description="Inside the place; '' = all of it")
    folder_date: date | None = Field(
        default=None,
        description="Confirmed on the Run screen; null = no folder date. "
        "Left out: read from the folder name.",
    )


class RunAccepted(BaseModel):
    run_id: uuid.UUID
    status: RunStatus
    folder: str
    folder_date: date | None


class StepTimingsOut(BaseModel):
    """Milliseconds per step (task 4.9); null = not measured or not run yet."""

    waiting_ms: int | None
    read_ms: int | None
    extract_ms: int | None
    coding_ms: int | None
    checks_ms: int | None
    total_ms: int | None

    @classmethod
    def of(cls, timings: StepTimings | None) -> "StepTimingsOut | None":
        return cls.model_validate(timings, from_attributes=True) if timings else None


class FileStatus(BaseModel):
    file: str  # path inside the selected folder
    status: str  # a document status, or QUEUED (not ingested yet)
    document_id: uuid.UUID | None
    document_type: DocumentType | None
    pages: int | None
    failure_reason: str | None
    from_earlier_run: bool
    timings: StepTimingsOut | None
    step: Step  # where the file is now (task 4.14)
    step_since: datetime | None  # when the current step started; null when not working
    queue_position: int | None  # waiting for the AI: 1 = next


class RunProgressOut(BaseModel):
    run_id: uuid.UUID
    client_id: str
    folder: str
    folder_date: date | None
    status: RunStatus
    failure_reason: str | None
    state: RunState
    created_at: datetime
    files_found: int
    counts: dict[str, int]
    files: list[FileStatus]
    typical_ms: int | None  # recent work time per file (for time left); null when finished
    force_stop: bool  # force stop asked: the read under way is being abandoned (task 4.20)
    server_time: datetime  # so the screen's timers use the server's clock


class TypicalTime(BaseModel):
    typical_ms: int | None  # recent work time per file; null before any file was measured


@router.get("/typical-time")
def get_typical_time(session: Annotated[Session, Depends(get_session)]) -> TypicalTime:
    """For "about 30 min" on the Run screen before a run starts (task 4.17)."""
    return TypicalTime(typical_ms=typical_work_ms(session))


def get_enqueue_scan() -> Callable[[str], object]:
    from app.workers.tasks import scan_run  # imported lazily: the API does not need a worker

    return scan_run.delay


@router.post("", status_code=status.HTTP_202_ACCEPTED)
def create_run(
    body: RunCreate,
    session: Annotated[Session, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    enqueue_scan: Annotated[Callable[[str], object], Depends(get_enqueue_scan)],
) -> RunAccepted:
    client = get_client(session, body.client_id)
    if client is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "client not found")
    try:
        folder_date = body.folder_date if "folder_date" in body.model_fields_set else FROM_FOLDER
        root = find_place(settings, client, body.place).path if body.place else None
        run = start_run(session, client=client, root=root, folder=body.folder,
                        folder_date=folder_date, today=settings.business_today())  # fmt: skip
        session.commit()
    except InboxError as exc:
        session.rollback()
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc

    enqueue_scan(str(run.id))  # after commit, so the worker always finds the run
    return RunAccepted(
        run_id=run.id, status=run.status, folder=run.folder, folder_date=run.folder_date
    )


@router.get("/{run_id}")
def get_run(run_id: uuid.UUID, session: Annotated[Session, Depends(get_session)]) -> RunProgressOut:
    """Live progress, per file the scan found."""
    return _progress_out(session, _found_run(session, run_id))


class RunQueue(Protocol):
    """Where a resumed run's work is queued (the worker; a fake in tests)."""

    def scan(self, run_id: str) -> object: ...
    def ingest(self, run_id: str, path: str) -> object: ...
    def step(self, document_id: str, status: DocumentStatus) -> object: ...


class _WorkerQueue:
    def scan(self, run_id: str) -> object:
        from app.workers.tasks import scan_run  # imported lazily: the API does not need a worker

        return scan_run.delay(run_id)

    def ingest(self, run_id: str, path: str) -> object:
        from app.workers.tasks import ingest_file

        return ingest_file.delay(run_id, path)

    def step(self, document_id: str, status: DocumentStatus) -> object:
        from app.workers.tasks import enqueue_next

        return enqueue_next(document_id, status)


def get_run_queue() -> RunQueue:
    return _WorkerQueue()


@router.post("/{run_id}/stop")
def stop(run_id: uuid.UUID, session: Annotated[Session, Depends(get_session)]) -> RunProgressOut:
    """Stop a running run (task 4.19): files not started stay as they are; a step already
    running finishes first. 409 if the run is not running."""
    run = _found_run(session, run_id, for_update=True)
    try:
        stop_run(run)
    except RunControlError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    session.commit()
    return _progress_out(session, run)


@router.post("/{run_id}/force-stop")
def force_stop(
    run_id: uuid.UUID, session: Annotated[Session, Depends(get_session)]
) -> RunProgressOut:
    """Abandon the model read still under way in a stopped run, within a few seconds (task
    4.20). 409 if the run is not stopped."""
    run = _found_run(session, run_id, for_update=True)
    try:
        force_stop_run(run)
    except RunControlError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    session.commit()
    return _progress_out(session, run)


@router.post("/{run_id}/resume")
def resume(
    run_id: uuid.UUID,
    session: Annotated[Session, Depends(get_session)],
    queue: Annotated[RunQueue, Depends(get_run_queue)],
) -> RunProgressOut:
    """Continue a stopped run from where each file is. 409 if the run is not stopped."""
    run = _found_run(session, run_id, for_update=True)
    try:
        plan = resume_run(session, run)
    except RunControlError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    session.commit()  # before queueing: the worker must see the run running again
    if plan.scan:
        queue.scan(str(run.id))
    for path in plan.ingest:
        queue.ingest(str(run.id), path)
    for document_id, document_status in plan.steps:
        queue.step(str(document_id), document_status)
    return _progress_out(session, run)


def _found_run(session: Session, run_id: uuid.UUID, *, for_update: bool = False) -> Run:
    run = session.get(Run, run_id, with_for_update=for_update)
    if run is None or get_client(session, run.client_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "run not found")
    return run


def _progress_out(session: Session, run: Run) -> RunProgressOut:
    progress = run_progress(session, run)
    return RunProgressOut(
        run_id=run.id,
        client_id=run.client_id,
        folder=run.folder,
        folder_date=run.folder_date,
        status=progress.status,
        failure_reason=run.failure_reason,
        state=progress.state,
        created_at=run.created_at,
        files_found=len(progress.files),
        counts=progress.counts,
        typical_ms=progress.typical_ms,
        force_stop=run.force_stop,
        server_time=datetime.now(UTC),
        files=[
            FileStatus(
                file=_inside(run.folder, f.path),
                status=f.status_label,
                document_id=f.document_id,
                document_type=f.document_type,
                pages=f.page_count,
                failure_reason=f.failure_reason,
                from_earlier_run=f.from_earlier_run,
                timings=StepTimingsOut.of(f.timings),
                step=f.step,
                step_since=f.step_since,
                queue_position=f.queue_position,
            )
            for f in progress.files
        ],
    )


def _inside(folder: str, path: str) -> str:
    """'October 2026/21/inv.pdf' shown as '21/inv.pdf' for a run on 'October 2026'."""
    full = PurePosixPath(path)
    return full.relative_to(folder).as_posix() if folder else full.as_posix()
