"""Declarative base, shared columns and column types for all tables."""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import DateTime, Enum, MetaData, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Stable constraint names so Alembic migrations stay reviewable and reversible.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    type_annotation_map = {  # noqa: RUF012 - SQLAlchemy reads this class attribute
        datetime: DateTime(timezone=True),
        dict[str, Any]: JSONB,
    }


def str_enum(enum_cls: type[StrEnum], name: str) -> Enum:
    """Text column + CHECK constraint (not a native PG enum, which is hard to change later)."""
    return Enum(
        enum_cls,
        name=name,
        native_enum=False,
        create_constraint=True,
        length=32,
        values_callable=lambda cls: [member.value for member in cls],
        validate_strings=True,
    )


class UuidPk:
    # Not guessable in URLs; the outbox id doubles as the posting idempotency key.
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4, sort_order=-3)


class ClientScoped:
    """Every table carries the client (accounting organisation) it belongs to."""

    client_id: Mapped[str] = mapped_column(String(64), sort_order=-2)


class Timestamps:
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), sort_order=100)
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now(), sort_order=100
    )
