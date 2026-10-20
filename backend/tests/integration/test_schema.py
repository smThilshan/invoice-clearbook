"""DB constraints from the initial migration: CHECKs, partial unique indexes, posting trigger."""

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Engine, create_engine, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.domain.enums import DocumentStatus, DocumentType
from app.models import Base, Document, Outbox, OutboxStatus
from tests.integration.conftest import alembic_config, create_fresh_database
from tests.integration.factories import CLIENT, make_document

pytestmark = pytest.mark.integration

NOT_APPROVED = [
    s for s in DocumentStatus if s not in (DocumentStatus.APPROVED, DocumentStatus.POSTING)
]
RELEASED = [
    DocumentStatus.REJECTED,
    DocumentStatus.DUPLICATE,
    DocumentStatus.FAILED,
    DocumentStatus.VOIDED,
]
KEY = {"vendor_key": "al-noor", "document_type": DocumentType.BILL, "business_key": "N:291"}


def test_migrations_upgrade_downgrade_upgrade() -> None:
    url = create_fresh_database(f"{get_settings().postgres_db}_test_migrations")
    config = alembic_config(url)

    command.upgrade(config, "head")
    command.downgrade(config, "base")
    command.upgrade(config, "head")

    engine = create_engine(url)
    with engine.connect() as conn:
        tables = conn.execute(
            text("SELECT count(*) FROM pg_tables WHERE schemaname = 'public'")
        ).scalar_one()
    engine.dispose()
    assert tables == len(Base.metadata.tables) + 1  # + alembic_version


def test_models_match_migrations(migrated_engine: Engine) -> None:
    with migrated_engine.connect() as conn:
        diff = compare_metadata(
            MigrationContext.configure(conn, opts={"compare_type": True}), Base.metadata
        )

    assert diff == []


def test_unknown_status_rejected_by_check(session: Session) -> None:
    document = make_document(session)

    with pytest.raises(IntegrityError, match="ck_documents_status"):
        session.execute(
            text("UPDATE documents SET status = 'BOGUS' WHERE id = :id"), {"id": document.id}
        )


def _set_status(session: Session, document: Document, status: DocumentStatus) -> None:
    """Raw update that bypasses the state machine, to prove the DB guards on its own."""
    session.execute(
        update(Document)
        .where(Document.id == document.id)
        .values(status=status)
        .execution_options(synchronize_session=False)
    )


@pytest.mark.parametrize("status", NOT_APPROVED)
def test_trigger_blocks_posting_unless_approved(session: Session, status: DocumentStatus) -> None:
    document = make_document(session, status=status)

    with pytest.raises(IntegrityError, match="only APPROVED can be posted"):
        _set_status(session, document, DocumentStatus.POSTING)


def test_trigger_allows_approved_to_posting(session: Session) -> None:
    document = make_document(session, status=DocumentStatus.APPROVED)

    _set_status(session, document, DocumentStatus.POSTING)


def test_trigger_blocks_insert_as_posting(session: Session) -> None:
    with pytest.raises(IntegrityError, match="cannot be created as POSTING"):
        make_document(session, status=DocumentStatus.POSTING)


def test_business_key_unique_among_live_documents(session: Session) -> None:
    make_document(session, status=DocumentStatus.IN_REVIEW, **KEY)

    with pytest.raises(IntegrityError, match="uq_documents_business_key"):
        make_document(session, status=DocumentStatus.VERIFIED, **KEY)


@pytest.mark.parametrize("released", RELEASED)
def test_business_key_released_by_dead_documents(
    session: Session, released: DocumentStatus
) -> None:
    make_document(session, status=released, **KEY)

    make_document(session, status=DocumentStatus.IN_REVIEW, **KEY)


def test_same_business_key_allowed_for_other_vendor_type_or_client(session: Session) -> None:
    make_document(session, **KEY)

    make_document(session, **{**KEY, "vendor_key": "other-vendor"})
    make_document(session, **{**KEY, "document_type": DocumentType.CREDIT_NOTE})
    make_document(session, **{**KEY, "client_id": "other-client"})


def test_same_file_twice_only_as_duplicate(session: Session) -> None:
    original = make_document(session)

    make_document(session, sha256=original.sha256, status=DocumentStatus.DUPLICATE)
    with pytest.raises(IntegrityError, match="uq_documents_client_id_sha256"):
        make_document(session, sha256=original.sha256)


def test_one_active_outbox_row_per_document(session: Session) -> None:
    document = make_document(session, status=DocumentStatus.APPROVED)
    session.add(Outbox(client_id=CLIENT, document_id=document.id, status=OutboxStatus.FAILED))
    session.add(Outbox(client_id=CLIENT, document_id=document.id))
    session.flush()

    session.add(Outbox(client_id=CLIENT, document_id=document.id))
    with pytest.raises(IntegrityError, match="uq_outbox_active_document_id"):
        session.flush()
