"""Integration fixtures: a throwaway `<db>_test` database, migrated once per test session.

Needs `make up`. Each test runs inside a transaction that is rolled back.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import URL, Engine, create_engine, text
from sqlalchemy.orm import Session

from app.core.config import get_settings

BACKEND_DIR = Path(__file__).resolve().parents[2]


def create_fresh_database(name: str) -> URL:
    base_url = get_settings().database_url
    admin = create_engine(base_url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
            conn.execute(text(f'CREATE DATABASE "{name}"'))
    finally:
        admin.dispose()
    return base_url.set(database=name)


def alembic_config(url: URL) -> Config:
    config = Config(BACKEND_DIR / "alembic.ini")
    config.attributes["database_url"] = url
    config.attributes["configure_logger"] = False
    return config


@pytest.fixture(scope="session")
def migrated_engine() -> Iterator[Engine]:
    url = create_fresh_database(f"{get_settings().postgres_db}_test")
    command.upgrade(alembic_config(url), "head")
    engine = create_engine(url)
    yield engine
    engine.dispose()


@pytest.fixture
def session(migrated_engine: Engine) -> Iterator[Session]:
    with migrated_engine.connect() as conn:
        outer = conn.begin()
        db_session = Session(bind=conn, join_transaction_mode="create_savepoint")
        yield db_session
        db_session.close()
        outer.rollback()
