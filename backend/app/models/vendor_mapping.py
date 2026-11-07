from datetime import datetime

from sqlalchemy import String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.enums import DocumentType, TaxType
from app.models.base import Base, ClientScoped, Timestamps, UuidPk, str_enum


class VendorMapping(UuidPk, ClientScoped, Timestamps, Base):
    """Learned from approvals: vendor -> account + tax type (architecture §9, source 1), and
    whether its documents are bills or expenses (decision 88)."""

    __tablename__ = "vendor_mappings"

    vendor_key: Mapped[str] = mapped_column(String(255))
    account_code: Mapped[str | None] = mapped_column(String(32))
    tax_type: Mapped[TaxType | None] = mapped_column(str_enum(TaxType, "tax_type"))
    consistent_approvals: Mapped[int] = mapped_column(default=0)
    last_approved_at: Mapped[datetime | None]
    payment_type: Mapped[DocumentType | None] = mapped_column(
        str_enum(DocumentType, "vendor_payment_type")
    )
    payment_approvals: Mapped[int] = mapped_column(default=0, server_default="0")

    __table_args__ = (UniqueConstraint("client_id", "vendor_key"),)
