"""Deleting a company permanently (decision 91): the second step after Remove, like deleting a
rejected document (decision 45).

Only a removed company can be deleted, by an admin who types its name. Its accounting
connection is disconnected first (our access is withdrawn there; nothing in the accounting
system is changed: posted bills stay). Then every row of the company goes in one transaction,
with one `deletions` row per document kept as proof, holding the ID of what was posted. The
caller commits, then removes the company's storage folder: at worst a stray folder, never a
record pointing to a file that is gone. Source folders (local, SharePoint) are never touched.
"""

from dataclasses import dataclass

from sqlalchemy import String, delete, func, insert, literal, select
from sqlalchemy.orm import Session

from app.adapters.accounting.registry import Registry
from app.core.config import Settings
from app.models import (
    AccountingConnection,
    AuditLog,
    Base,
    Client,
    Contact,
    Correction,
    Deletion,
    Document,
    Extraction,
    LedgerAccount,
    Outbox,
    Run,
    TaxRate,
    VendorMapping,
)
from app.services.accounting_connections import connection_of, disconnect
from app.services.clients import ClientError, busy_with

# Every table holding a company's rows, in an order its foreign keys allow. `deletions` is the
# proof and stays. A new table with client_id must be added here (a test checks).
COMPANY_TABLES: tuple[type[Base], ...] = (
    Correction,
    Extraction,
    Outbox,
    AuditLog,
    Document,
    Run,
    VendorMapping,
    Contact,
    LedgerAccount,
    TaxRate,
    AccountingConnection,
    Client,
)
REASON = "company deleted"
PROOF_COLUMNS = ["client_id", "document_id", "sha256", "page_count", "rejected_reason",
                 "external_id", "deleted_by"]  # fmt: skip


@dataclass(frozen=True)
class DeletionPreview:
    documents: int
    posted: int  # in the accounting system: they stay there
    connected_to: str | None  # the system disconnected first, by name
    blocked: str | None  # why it cannot be deleted now; None = it can


def removed_client(session: Session, client_id: str, *, for_update: bool = False) -> Client | None:
    query = select(Client).where(Client.client_id == client_id, Client.is_active.is_(False))
    return session.scalars(query.with_for_update() if for_update else query).first()


def preview(session: Session, registry: Registry, client: Client) -> DeletionPreview:
    """What deleting the company would remove, for the admin to confirm."""
    documents, posted = session.execute(
        select(func.count(), func.count(Document.external_id)).where(
            Document.client_id == client.client_id
        )
    ).one()
    connection = connection_of(session, client.client_id)
    entry = registry.get(connection.provider) if connection else None
    return DeletionPreview(
        documents=documents,
        posted=posted,
        connected_to=(entry.name if entry else connection.provider.value) if connection else None,
        blocked=busy_with(session, client.client_id),
    )


def delete_company(
    session: Session,
    settings: Settings,
    registry: Registry,
    client: Client,
    *,
    typed_name: str,
    actor: str,
) -> int:
    """Delete everything of a removed company; the number of documents deleted. The caller
    commits, then removes `client_folder(client_id)` from storage."""
    if client.is_active:
        raise ClientError("remove the company first: only a removed company can be deleted")
    if typed_name.strip() != client.name:
        raise ClientError("the name typed does not match the company's name")
    reason = busy_with(session, client.client_id)
    if reason:
        raise ClientError(f"{client.name} cannot be deleted now: {reason}")
    client_id = client.client_id

    documents = session.scalar(
        select(func.count()).select_from(Document).where(Document.client_id == client_id)
    )
    disconnect(session, settings, registry, client_id)  # withdraws our access there, if it can
    session.execute(
        insert(Deletion).from_select(
            PROOF_COLUMNS,
            select(
                Document.client_id,
                Document.id,
                Document.sha256,
                Document.page_count,
                literal(REASON, String),
                Document.external_id,
                literal(actor, String),
            ).where(Document.client_id == client_id),
        )
    )
    for model in COMPANY_TABLES:
        session.execute(delete(model).where(model.client_id == client_id))  # type: ignore[attr-defined]
    return documents or 0
