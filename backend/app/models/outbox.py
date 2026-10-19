import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import ForeignKey, Index, String, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, ClientScoped, Timestamps, UuidPk, str_enum


class OutboxStatus(StrEnum):
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    DONE = "DONE"
    FAILED = "FAILED"  # business error: document went back to review
    DEAD = "DEAD"  # unknown error: needs a human


ACTIVE_OUTBOX = (OutboxStatus.PENDING, OutboxStatus.IN_PROGRESS)


class Outbox(UuidPk, ClientScoped, Timestamps, Base):
    """An approved document waiting to post. Written in the same transaction as APPROVED.

    `id` is the idempotency key sent to the accounting system.
    """

    __tablename__ = "outbox"

    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id", ondelete="RESTRICT"))
    status: Mapped[OutboxStatus] = mapped_column(
        str_enum(OutboxStatus, "status"), default=OutboxStatus.PENDING
    )
    step: Mapped[str | None] = mapped_column(String(32))  # last completed posting sub-step
    progress: Mapped[dict[str, Any]] = mapped_column(default=dict)  # sub-step results to resume
    attempts: Mapped[int] = mapped_column(default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(server_default=func.now())
    last_error: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        # Worker pickup: SELECT ... WHERE status = 'PENDING' AND next_attempt_at <= now()
        Index(None, "status", "next_attempt_at"),
        # A document is posted by at most one live outbox row.
        Index(
            "uq_outbox_active_document_id",
            "document_id",
            unique=True,
            postgresql_where=text(
                "status IN ({})".format(", ".join(f"'{s.value}'" for s in ACTIVE_OUTBOX))
            ),
        ),
    )
