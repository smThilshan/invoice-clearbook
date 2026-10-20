import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, ForeignKey, Identity, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.enums import DocumentStatus
from app.models.base import Base, ClientScoped, str_enum


class AuditLog(ClientScoped, Base):
    """Append-only history of document status changes. Written by `state_machine.transition()`."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True, sort_order=-3)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id", ondelete="RESTRICT"))
    from_status: Mapped[DocumentStatus] = mapped_column(str_enum(DocumentStatus, "from_status"))
    to_status: Mapped[DocumentStatus] = mapped_column(str_enum(DocumentStatus, "to_status"))
    actor: Mapped[str] = mapped_column(String(128))  # user name, or "system:<step>"
    reason: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict[str, Any]] = mapped_column(default=dict)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    __table_args__ = (Index(None, "document_id", "created_at"),)
