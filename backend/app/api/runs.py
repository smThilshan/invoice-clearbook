"""Runs: process one typed inbox folder for one date.

No login yet (task 4.1): the API listens on localhost only.
"""

import uuid
from collections.abc import Callable
from datetime import date, datetime
from pathlib import PurePosixPath
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.db import get_session
from app.domain.enums import Direction, DocumentType
from app.models import Run, RunStatus
from app.pipeline.ingest import INBOX_FOLDERS, InboxError
from app.services.runs import RunState, run_progress, start_run

router = APIRouter(prefix="/runs", tags=["runs"])

FOLDERS: dict[str, Direction] = {
    "Purchase": Direction.PURCHASE,
    "Sales": Direction.SALES,
    "Unsorted": Direction.UNSORTED,
}


class RunCreate(BaseModel):
    folder: Literal["Purchase", "Sales", "Unsorted"]
    date: date


class RunAccepted(BaseModel):
    run_id: uuid.UUID
    status: RunStatus


class FileStatus(BaseModel):
    file: str  # name within the run folder
    status: str  # a document status, or QUEUED (not ingested yet)
    document_id: uuid.UUID | None
    document_type: DocumentType | None
    pages: int | None
    failure_reason: str | None
    from_earlier_run: bool


class RunProgressOut(BaseModel):
    run_id: uuid.UUID
    folder: str
    date: date
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
    try:
        with session.begin():
            run = start_run(
                session,
                client_id=settings.default_client_id,
                direction=FOLDERS[body.folder],
                folder_date=body.date,
                inbox_dir=settings.inbox_dir,
            )
    except InboxError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc

    enqueue_scan(str(run.id))  # after commit, so the worker always finds the run
    return RunAccepted(run_id=run.id, status=run.status)


@router.get("/{run_id}")
def get_run(
    run_id: uuid.UUID,
    session: Annotated[Session, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> RunProgressOut:
    """Live progress, per file the scan found."""
    run = session.get(Run, run_id)
    if run is None or run.client_id != settings.default_client_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "run not found")
    progress = run_progress(session, run)
    return RunProgressOut(
        run_id=run.id,
        folder=INBOX_FOLDERS[run.direction],
        date=run.folder_date,
        state=progress.state,
        created_at=run.created_at,
        files_found=len(progress.files),
        counts=progress.counts,
        files=[
            FileStatus(
                file=PurePosixPath(f.path).name,
                status=f.status_label,
                document_id=f.document_id,
                document_type=f.document_type,
                pages=f.page_count,
                failure_reason=f.failure_reason,
                from_earlier_run=f.from_earlier_run,
            )
            for f in progress.files
        ],
    )
