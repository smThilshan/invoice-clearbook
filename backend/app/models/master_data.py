"""Master data from the client's accounting system: ledger accounts, tax rates, contacts.

Neutral on purpose (decision 20): filled by whichever adapter the client uses (CSV import, Xero
sync, ...) and read by coding, validation, vendor match and the Workbench, which never know the
source. `external_id` is the provider's own ID (a CSV import uses the code); `raw` keeps the
provider's full record so later tasks can use more fields without a migration.
"""

from decimal import Decimal
from typing import Any

from sqlalchemy import Index, Numeric, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.enums import AccountClass, AccountingProvider, TaxType
from app.models.base import Base, ClientScoped, Timestamps, UuidPk, str_enum


class MasterDataRow(UuidPk, ClientScoped, Timestamps):
    """Columns shared by all master data tables. Row identity: (client, provider, external_id)."""

    provider: Mapped[AccountingProvider] = mapped_column(
        str_enum(AccountingProvider, "provider"), sort_order=-1
    )
    external_id: Mapped[str] = mapped_column(String(64), sort_order=-1)
    is_active: Mapped[bool] = mapped_column(default=True)
    raw: Mapped[dict[str, Any]] = mapped_column(default=dict)


class LedgerAccount(MasterDataRow, Base):
    __tablename__ = "ledger_accounts"

    code: Mapped[str | None] = mapped_column(String(32))  # what invoice lines reference
    name: Mapped[str] = mapped_column(String(255))
    account_class: Mapped[AccountClass] = mapped_column(str_enum(AccountClass, "account_class"))
    default_tax_code: Mapped[str | None] = mapped_column(String(64))  # -> tax_rates.code

    __table_args__ = (
        UniqueConstraint("client_id", "provider", "external_id"),
        Index(
            "uq_ledger_accounts_code",
            "client_id",
            "provider",
            "code",
            unique=True,
            postgresql_where=text("code IS NOT NULL"),
        ),
    )


class TaxRate(MasterDataRow, Base):
    __tablename__ = "tax_rates"

    code: Mapped[str] = mapped_column(String(64))  # the provider's code, sent when posting
    name: Mapped[str] = mapped_column(String(255))
    rate: Mapped[Decimal | None] = mapped_column(Numeric(9, 4))  # percent, e.g. 5.0000
    # Canonical meaning; null = not mapped yet, so coding cannot use it.
    tax_type: Mapped[TaxType | None] = mapped_column(str_enum(TaxType, "tax_type"))

    __table_args__ = (
        UniqueConstraint("client_id", "provider", "external_id"),
        UniqueConstraint("client_id", "provider", "code"),
    )


class Contact(MasterDataRow, Base):
    __tablename__ = "contacts"

    name: Mapped[str] = mapped_column(String(255))
    tax_number: Mapped[str | None] = mapped_column(String(32))  # TRN for UAE vendors
    is_supplier: Mapped[bool] = mapped_column(default=False)

    __table_args__ = (
        UniqueConstraint("client_id", "provider", "external_id"),
        Index(None, "client_id", "tax_number"),
        # Fuzzy vendor match (task 3.5).
        Index(
            "ix_contacts_name_trgm",
            "name",
            postgresql_using="gin",
            postgresql_ops={"name": "gin_trgm_ops"},
        ),
    )
