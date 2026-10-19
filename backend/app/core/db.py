"""Database engine and sessions (SQLAlchemy 2.0, sync).

Sync on purpose: the same pipeline/service code runs in FastAPI (thread pool) and in Celery workers.
Transactions are explicit: services open them with `session.begin()` or use `session_scope()`.
"""

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings

logger = logging.getLogger(__name__)

_CONNECT_TIMEOUT_SECONDS = 5


@lru_cache
def get_engine() -> Engine:
    return create_engine(
        get_settings().database_url,
        pool_pre_ping=True,
        connect_args={"connect_timeout": _CONNECT_TIMEOUT_SECONDS},
    )


@lru_cache
def get_session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False)


def get_session() -> Iterator[Session]:
    """FastAPI dependency: one session per request. Does not commit; services own transactions."""
    with get_session_factory()() as session:
        yield session


@contextmanager
def session_scope() -> Iterator[Session]:
    """Unit of work for workers and services: commit on success, roll back on error."""
    with get_session_factory().begin() as session:
        yield session


def ping(session: Session) -> bool:
    try:
        session.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        # One line, no traceback: probes poll this often and an outage would flood the log.
        logger.warning("database ping failed: %s", type(exc).__name__)
        return False
    return True


def dispose_engine() -> None:
    if get_engine.cache_info().currsize:
        get_engine().dispose()
    get_session_factory.cache_clear()
    get_engine.cache_clear()
