from sqlalchemy import String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.enums import AccountingProvider
from app.models.base import Base, ClientScoped, Timestamps, UuidPk, str_enum


class Client(UuidPk, ClientScoped, Timestamps, Base):
    """A business whose books are kept (decision 25; any kind of business, decision 35).

    `client_id` is the short id used on every other table. `inbox_path` is the client's own
    folder, if it has one: offered as a starting place on the Run screen. Runs may also read
    from any other allowed place (decision 35).
    """

    __tablename__ = "clients"

    name: Mapped[str] = mapped_column(String(255))
    inbox_path: Mapped[str | None] = mapped_column(Text)  # absolute; null = no own folder
    accounting_provider: Mapped[AccountingProvider] = mapped_column(
        str_enum(AccountingProvider, "accounting_provider"), default=AccountingProvider.MANUAL
    )
    is_active: Mapped[bool] = mapped_column(default=True)

    __table_args__ = (UniqueConstraint("client_id"),)
