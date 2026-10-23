"""Documents and what was learned about them: model extractions and accountant corrections.

JSONB holds `Invoice.model_dump(mode="json")`, so money is stored as strings and stays exact.
"""

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import BigInteger, ForeignKey, Index, String, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.enums import Direction, DocumentStatus, DocumentType, QueueCategory
from app.models.base import Base, ClientScoped, Timestamps, UuidPk, str_enum

# A document in one of these statuses no longer holds its business key (decision 18).
BUSINESS_KEY_RELEASED = (
    DocumentStatus.REJECTED,
    DocumentStatus.DUPLICATE,
    DocumentStatus.FAILED,
    DocumentStatus.VOIDED,
)


def _not_in(statuses: tuple[DocumentStatus, ...]) -> str:
    return "status NOT IN ({})".format(", ".join(f"'{s.value}'" for s in statuses))


class Document(UuidPk, ClientScoped, Timestamps, Base):
    """One captured file. `status` changes only through `domain.state_machine.transition()`."""

    __tablename__ = "documents"

    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.id", ondelete="RESTRICT"))
    direction: Mapped[Direction] = mapped_column(str_enum(Direction, "direction"))
    folder_date: Mapped[date | None]  # the run's folder date; null = none known
    source_path: Mapped[str] = mapped_column(Text)  # relative to the client's inbox folder
    storage_key: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String(64))
    mime_type: Mapped[str] = mapped_column(String(100))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    page_count: Mapped[int | None]  # set by the Read step

    status: Mapped[DocumentStatus] = mapped_column(
        str_enum(DocumentStatus, "status"), default=DocumentStatus.CAPTURED
    )
    document_type: Mapped[DocumentType | None] = mapped_column(
        str_enum(DocumentType, "document_type")
    )
    queue: Mapped[QueueCategory | None] = mapped_column(str_enum(QueueCategory, "queue"))

    # Current working copy of the Invoice: the latest extraction plus accountant edits.
    invoice: Mapped[dict[str, Any] | None]
    issues: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    # Per line: suggested account/tax code with its source and confidence (task 3.6). The
    # codes themselves are in `invoice.lines`; this says where they came from.
    coding: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    # Invoice fields the accountant typed or confirmed (decision 24): human-verified, so no
    # longer grounded against the page text.
    verified_fields: Mapped[list[str]] = mapped_column(JSONB, default=list, server_default="[]")
    vendor_key: Mapped[str | None] = mapped_column(String(255))
    business_key: Mapped[str | None] = mapped_column(String(255))
    # The known supplier (master data) this document was matched to; null = new or unmatched.
    contact_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("contacts.id", ondelete="SET NULL")
    )
    failure_reason: Mapped[str | None] = mapped_column(Text)
    # The pipeline step a worker is running on this document now, and since when (task 4.14,
    # live progress). Set just before the step starts; it only counts while the status is still
    # the one that step works on: a later status means the step is done.
    working_step: Mapped[str | None] = mapped_column(String(16))
    working_since: Mapped[datetime | None]
    external_id: Mapped[str | None] = mapped_column(
        String(64), comment="ID of the posted document in the accounting system"
    )

    __table_args__ = (
        Index(None, "client_id", "status"),
        Index(None, "run_id"),
        Index(None, "client_id", "source_path"),  # run progress: file -> its document
        # Same file captured twice: the second copy is kept as DUPLICATE.
        Index(
            "uq_documents_client_id_sha256",
            "client_id",
            "sha256",
            unique=True,
            postgresql_where=text(f"status <> '{DocumentStatus.DUPLICATE.value}'"),
        ),
        # Decision 18: one live document per business key.
        Index(
            "uq_documents_business_key",
            "client_id",
            "vendor_key",
            "document_type",
            "business_key",
            unique=True,
            postgresql_where=text(f"business_key IS NOT NULL AND {_not_in(BUSINESS_KEY_RELEASED)}"),
        ),
    )


class Extraction(UuidPk, ClientScoped, Base):
    """Model output for one attempt. Never edited."""

    __tablename__ = "extractions"

    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"))
    attempt: Mapped[int]
    model: Mapped[str] = mapped_column(String(128))
    prompt_version: Mapped[str] = mapped_column(String(32))
    schema_version: Mapped[int]
    raw_output: Mapped[str] = mapped_column(Text)
    data: Mapped[dict[str, Any] | None]  # null when the output did not parse
    error: Mapped[str | None] = mapped_column(Text)
    duration_ms: Mapped[int | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    __table_args__ = (UniqueConstraint("document_id", "attempt"),)


class Correction(UuidPk, ClientScoped, Base):
    """One field the accountant changed: what the model said vs the final value. One row per
    document and field, kept in step with every save (task 4.7)."""

    __tablename__ = "corrections"

    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"))
    extraction_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("extractions.id", ondelete="SET NULL")
    )
    field: Mapped[str] = mapped_column(String(128))  # e.g. "total", "lines[0].account_code"
    model_value: Mapped[Any] = mapped_column(JSONB, nullable=True)
    final_value: Mapped[Any] = mapped_column(JSONB, nullable=True)
    vendor_key: Mapped[str | None] = mapped_column(String(255))
    model: Mapped[str | None] = mapped_column(String(128))
    prompt_version: Mapped[str | None] = mapped_column(String(32))
    corrected_by: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    __table_args__ = (
        UniqueConstraint("document_id", "field"),  # also serves lookups by document
        Index(None, "client_id", "vendor_key"),
    )
