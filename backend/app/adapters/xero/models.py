"""Local copy of Xero master data, synced on demand (task 1.6).

Lives in the adapter: the rest of the app reads it only through the adapter's canonical API.
`raw` keeps the full Xero object so later tasks can use more fields without a migration.
"""

from decimal import Decimal
from typing import Any

from sqlalchemy import Index, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, ClientScoped, Timestamps, UuidPk


class XeroAccount(UuidPk, ClientScoped, Timestamps, Base):
    __tablename__ = "xero_accounts"

    xero_id: Mapped[str] = mapped_column(String(64))  # AccountID
    code: Mapped[str | None] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(255))
    type: Mapped[str] = mapped_column(String(32))  # EXPENSE, FIXED, REVENUE, ...
    account_class: Mapped[str | None] = mapped_column(String(16))  # ASSET, EXPENSE, ...
    tax_type: Mapped[str | None] = mapped_column(String(64))  # default tax rate
    status: Mapped[str] = mapped_column(String(16))  # ACTIVE, ARCHIVED
    raw: Mapped[dict[str, Any]]

    __table_args__ = (
        UniqueConstraint("client_id", "xero_id"),
        Index(None, "client_id", "code"),
    )


class XeroTaxRate(UuidPk, ClientScoped, Timestamps, Base):
    __tablename__ = "xero_tax_rates"

    tax_type: Mapped[str] = mapped_column(String(64))  # Xero's code, unique per organisation
    name: Mapped[str] = mapped_column(String(255))
    rate: Mapped[Decimal | None] = mapped_column(Numeric(9, 4))  # effective rate, e.g. 5.0000
    status: Mapped[str] = mapped_column(String(16))
    raw: Mapped[dict[str, Any]]

    __table_args__ = (UniqueConstraint("client_id", "tax_type"),)


class XeroContact(UuidPk, ClientScoped, Timestamps, Base):
    __tablename__ = "xero_contacts"

    xero_id: Mapped[str] = mapped_column(String(64))  # ContactID
    name: Mapped[str] = mapped_column(String(255))
    tax_number: Mapped[str | None] = mapped_column(String(32))  # TRN for UAE vendors
    is_supplier: Mapped[bool] = mapped_column(default=False)
    status: Mapped[str] = mapped_column(String(16))
    raw: Mapped[dict[str, Any]]

    __table_args__ = (
        UniqueConstraint("client_id", "xero_id"),
        Index(None, "client_id", "tax_number"),
        # Fuzzy vendor match (task 3.5).
        Index(
            "ix_xero_contacts_name_trgm",
            "name",
            postgresql_using="gin",
            postgresql_ops={"name": "gin_trgm_ops"},
        ),
    )
