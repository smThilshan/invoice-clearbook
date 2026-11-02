from datetime import datetime

from sqlalchemy import String, Text, UniqueConstraint, false
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.enums import AccountingProvider
from app.models.base import Base, ClientScoped, Timestamps, UuidPk, str_enum


class AccountingConnection(UuidPk, ClientScoped, Timestamps, Base):
    """A client's connection to its accounting system (task 5.3): which organisation, and the
    tokens to act on it. Tokens are stored encrypted (settings.token_key), never logged."""

    __tablename__ = "accounting_connections"

    provider: Mapped[AccountingProvider] = mapped_column(str_enum(AccountingProvider, "provider"))
    tenant_id: Mapped[str] = mapped_column(String(64))  # the organisation in that system
    tenant_name: Mapped[str] = mapped_column(String(255))
    org_ref: Mapped[str | None] = mapped_column(String(64))  # for links into its screens (70)
    connection_id: Mapped[str | None] = mapped_column(String(64))  # needed to disconnect
    access_token: Mapped[str] = mapped_column(Text)  # encrypted
    refresh_token: Mapped[str] = mapped_column(Text)  # encrypted
    expires_at: Mapped[datetime]  # when the access token stops working
    scopes: Mapped[str] = mapped_column(Text, default="")
    connected_by: Mapped[str | None] = mapped_column(String(128))
    # Decision 68: the provider refused the stored access for good; sending pauses until someone
    # connects again. The reason is for the screen (no secrets).
    needs_reconnect: Mapped[bool] = mapped_column(default=False, server_default=false())
    reconnect_reason: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (UniqueConstraint("client_id", "provider"),)
