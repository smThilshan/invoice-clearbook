"""Runs: process one typed inbox folder for one date.

No login yet (task 4.1): the API listens on localhost only.
"""

import uuid
from collections.abc import Callable
from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.db import get_session
from app.domain.enums import Direction
from app.models import RunStatus
from app.pipeline.ingest import InboxError
from app.services.runs import start_run

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
