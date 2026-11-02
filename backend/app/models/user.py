from datetime import datetime

from sqlalchemy import Index, String, func, true
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.enums import Role
from app.models.base import Base, Timestamps, UuidPk, str_enum


class User(UuidPk, Timestamps, Base):
    """A Missan staff member who logs in to the Workbench (decision 71).

    The one table without client_id (rule 10): users belong to Missan and work on every client;
    they are not client data. Only the Argon2 hash of the password is stored."""

    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(254))  # stored lower-case
    name: Mapped[str] = mapped_column(String(128))
    role: Mapped[Role] = mapped_column(str_enum(Role, "role"))
    password_hash: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(default=True, server_default=true())
    # Raised on a password change or deactivation: every session signed before stops working.
    token_version: Mapped[int] = mapped_column(default=0, server_default="0")
    last_login_at: Mapped[datetime | None]

    __table_args__ = (Index("uq_users_email", func.lower(email), unique=True),)
