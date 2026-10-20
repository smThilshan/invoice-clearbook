"""Alembic environment. Target DB: `config.attributes["database_url"]` (tests) or app settings."""

from logging.config import fileConfig
from typing import Any

from alembic import context
from alembic.autogenerate.api import AutogenContext
from sqlalchemy import Enum, create_engine, pool
from sqlalchemy.sql.elements import conv

from app.core.config import get_settings
from app.models import Base

config = context.config
if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _is_enum_check(constraint: Any) -> bool:
    rule = getattr(constraint, "_create_rule", None)
    return isinstance(getattr(getattr(rule, "func", None), "__self__", None), Enum)


def render_item(type_: str, obj: Any, autogen_context: AutogenContext) -> str | bool | None:
    """Render text enums (`models.base.str_enum`) as VARCHAR + exactly one named CHECK.

    With SQLAlchemy 2.1 Alembic no longer spots enum-bound CHECKs, so it renders the type as
    sa.Enum(create_constraint=True) plus a copy of the CHECK under the enum's bare name. We emit
    the column as VARCHAR, keep the CHECK named by our convention and drop the bare-named copy.
    """
    if type_ == "type" and isinstance(obj, Enum) and not obj.native_enum:
        return f"sa.String(length={obj.length})"
    if type_ == "check" and _is_enum_check(obj) and not isinstance(obj.name, conv):
        return None  # skip
    return False  # default rendering


def run_migrations_offline() -> None:
    context.configure(
        url=get_settings().database_url.render_as_string(hide_password=False),
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    url = config.attributes.get("database_url") or get_settings().database_url
    engine = create_engine(url, poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            render_item=render_item,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
