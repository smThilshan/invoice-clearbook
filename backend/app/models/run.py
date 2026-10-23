from datetime import date, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.enums import Direction
from app.models.base import Base, ClientScoped, Timestamps, UuidPk, str_enum


class RunStatus(StrEnum):
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    STOPPED = "STOPPED"  # the user stopped it (task 4.19); Resume continues it


class Run(UuidPk, ClientScoped, Timestamps, Base):
    """One click on Run in the Workbench: a folder the user picked, sub-folders included.

    Decision 35: the folder may be anywhere inside the allowed places (this Mac, shared
    drives, the client's own folder). `root_path` is the place it was picked in; `folder` and
    every document's `source_path` are relative to it. Runs from before decision 35 have no
    root: they are relative to the client's folder (decision 25)."""

    __tablename__ = "runs"

    # The place the folder was picked in (absolute); null = the client's folder (older runs).
    root_path: Mapped[str | None] = mapped_column(Text)
    # Relative to `root_path`; "" = the whole place.
    folder: Mapped[str] = mapped_column(Text, default="", server_default="")
    direction: Mapped[Direction] = mapped_column(
        str_enum(Direction, "direction"), default=Direction.PURCHASE
    )
    # From the folder name when it holds one (month folder = month end), or set by the user;
    # null = no date, so the period check is skipped.
    folder_date: Mapped[date | None]
    status: Mapped[RunStatus] = mapped_column(
        str_enum(RunStatus, "status"), default=RunStatus.RUNNING
    )
    files_found: Mapped[int] = mapped_column(default=0)
    # Root-relative paths listed by the scan; null = not scanned yet. Per file progress.
    files: Mapped[list[Any] | None] = mapped_column(JSONB)
    created_by: Mapped[str | None] = mapped_column(String(128))
    # Why the run could not proceed (e.g. the shared folder was unreachable); null = fine.
    failure_reason: Mapped[str | None] = mapped_column(Text)
    finished_at: Mapped[datetime | None]

    __table_args__ = (Index(None, "client_id", "folder_date"),)
