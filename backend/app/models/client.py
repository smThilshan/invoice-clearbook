from sqlalchemy import String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.enums import AccountingProvider, ExpensesSentAs, LineGrouping
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
    # How approved lines are sent (decision 73); copied onto each outbox row at approval.
    line_grouping: Mapped[LineGrouping] = mapped_column(
        str_enum(LineGrouping, "line_grouping"),
        default=LineGrouping.BY_ITEM,
        server_default=LineGrouping.BY_ITEM.value,
    )
    # How expenses are sent (decision 89), and the account they are usually paid through: a
    # ledger account code marked `PAYS`; null = chosen on each expense.
    expenses_sent_as: Mapped[ExpensesSentAs] = mapped_column(
        str_enum(ExpensesSentAs, "expenses_sent_as"),
        default=ExpensesSentAs.BILL,
        server_default=ExpensesSentAs.BILL.value,
    )
    paid_through: Mapped[str | None] = mapped_column(String(64))
    # Its own TRNs (decision 92), 15 digits each: one per legal entity whose invoices it gets.
    # On a document, one of these is the customer's TRN, never the supplier's.
    own_trns: Mapped[list[str]] = mapped_column(JSONB, default=list, server_default="[]")

    __table_args__ = (UniqueConstraint("client_id"),)
