from datetime import date, datetime
from enum import StrEnum

from sqlalchemy import Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.enums import Direction
from app.models.base import Base, ClientScoped, Timestamps, UuidPk, str_enum


class RunStatus(StrEnum):
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class Run(UuidPk, ClientScoped, Timestamps, Base):
    """One click on Run in the Workbench: a typed inbox folder for one date."""

    __tablename__ = "runs"

    direction: Mapped[Direction] = mapped_column(str_enum(Direction, "direction"))
    folder_date: Mapped[date]
    status: Mapped[RunStatus] = mapped_column(
        str_enum(RunStatus, "status"), default=RunStatus.RUNNING
    )
    files_found: Mapped[int] = mapped_column(default=0)
    created_by: Mapped[str | None] = mapped_column(String(128))
    finished_at: Mapped[datetime | None]

    __table_args__ = (Index(None, "client_id", "folder_date"),)
