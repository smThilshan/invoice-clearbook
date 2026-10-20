"""Start a run: one typed inbox folder for one date."""

from datetime import date
from pathlib import Path

from sqlalchemy.orm import Session

from app.domain.enums import Direction
from app.models import Run
from app.pipeline.ingest import InboxError, run_folder


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
