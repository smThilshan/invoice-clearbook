import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Identity, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, ClientScoped


class Deletion(ClientScoped, Base):
    """Proof that a document was deleted permanently (decision 45): when, by whom, why. Holds
    no invoice content and no file name (names can carry supplier names): only the file's
    fingerprint, so the same file can be recognised if it comes back."""

    __tablename__ = "deletions"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True, sort_order=-3)
    document_id: Mapped[uuid.UUID]  # no foreign key: the document is gone
    sha256: Mapped[str] = mapped_column(String(64))
    page_count: Mapped[int | None]
    rejected_reason: Mapped[str | None] = mapped_column(String(64))
    deleted_by: Mapped[str] = mapped_column(String(128))
    deleted_at: Mapped[datetime] = mapped_column(server_default=func.now())
