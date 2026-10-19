from datetime import datetime

from sqlalchemy import String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.enums import TaxType
from app.models.base import Base, ClientScoped, Timestamps, UuidPk, str_enum


class VendorMapping(UuidPk, ClientScoped, Timestamps, Base):
    """Coding learned from approvals: vendor -> account + tax type (architecture §9, source 1)."""

    __tablename__ = "vendor_mappings"

    vendor_key: Mapped[str] = mapped_column(String(255))
    account_code: Mapped[str | None] = mapped_column(String(32))
    tax_type: Mapped[TaxType | None] = mapped_column(str_enum(TaxType, "tax_type"))
    consistent_approvals: Mapped[int] = mapped_column(default=0)
    last_approved_at: Mapped[datetime | None]

    __table_args__ = (UniqueConstraint("client_id", "vendor_key"),)
