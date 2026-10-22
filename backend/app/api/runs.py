"""Runs: process a folder the user picked inside a restaurant's folder (decision 25).

No login yet (task 4.1): the API listens on localhost only.
"""

import uuid
from collections.abc import Callable
from datetime import date, datetime
from pathlib import PurePosixPath
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.db import get_session
from app.domain.enums import DocumentType
from app.models import Run, RunStatus
from app.pipeline.folders import InboxError
from app.services.clients import get_client
from app.services.runs import FROM_FOLDER, RunState, run_progress, start_run
from app.services.timings import StepTimings

router = APIRouter(prefix="/runs", tags=["runs"])


class RunCreate(BaseModel):
    client_id: str
    folder: str = Field(default="", description="Inside the client's folder; '' = all of it")
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
        run = start_run(session, client=client, folder=body.folder, folder_date=folder_date,
                        today=settings.business_today())  # fmt: skip
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
    run = session.get(Run, run_id)
    if run is None or get_client(session, run.client_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "run not found")
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
            )
            for f in progress.files
        ],
    )


def _inside(folder: str, path: str) -> str:
    """'October 2026/21/inv.pdf' shown as '21/inv.pdf' for a run on 'October 2026'."""
    full = PurePosixPath(path)
    return full.relative_to(folder).as_posix() if folder else full.as_posix()
