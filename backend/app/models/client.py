from sqlalchemy import String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.enums import AccountingProvider
from app.models.base import Base, ClientScoped, Timestamps, UuidPk, str_enum


class Client(UuidPk, ClientScoped, Timestamps, Base):
    """A business whose books are kept: for Missan, one restaurant (decision 25).

    `client_id` is the short id used on every other table. `inbox_path` is the restaurant's
    folder on the shared drive; runs only ever read inside it.
    """

    __tablename__ = "clients"

    name: Mapped[str] = mapped_column(String(255))
    inbox_path: Mapped[str] = mapped_column(Text)  # absolute path on the shared drive
    accounting_provider: Mapped[AccountingProvider] = mapped_column(
        str_enum(AccountingProvider, "accounting_provider"), default=AccountingProvider.MANUAL
    )
    is_active: Mapped[bool] = mapped_column(default=True)

    __table_args__ = (UniqueConstraint("client_id"),)
