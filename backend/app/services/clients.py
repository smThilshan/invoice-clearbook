"""Clients (decision 25): one per restaurant, each with its folder on the shared drive."""

import re
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.enums import AccountingProvider
from app.models import Client
from app.pipeline.folders import InboxError, list_subfolders, resolve_inside

CLIENT_ID = re.compile(r"[a-z0-9][a-z0-9-]{1,62}[a-z0-9]")  # e.g. "cafe-al-noor"


class ClientError(Exception):
    pass


def add_client(
    session: Session,
    *,
    client_id: str,
    name: str,
    inbox_path: Path,
    accounting_provider: AccountingProvider = AccountingProvider.MANUAL,
) -> Client:
    """Register a restaurant. Its folder must exist and be readable now."""
    if not CLIENT_ID.fullmatch(client_id):
        raise ClientError("client id: lowercase letters, digits and dashes, e.g. cafe-al-noor")
    if not name.strip():
        raise ClientError("name is required")
    if not inbox_path.is_absolute():
        raise ClientError("the inbox folder must be an absolute path")
    if not inbox_path.is_dir():
        raise ClientError(f"the inbox folder does not exist or is not reachable: {inbox_path}")
    try:
        list_subfolders(inbox_path)
    except InboxError as exc:
        raise ClientError(f"the inbox folder cannot be read: {exc}") from exc
    if session.scalars(select(Client).where(Client.client_id == client_id)).first():
        raise ClientError(f"client {client_id!r} already exists")

    client = Client(
        client_id=client_id,
        name=name.strip(),
        inbox_path=str(inbox_path),
        accounting_provider=accounting_provider,
    )
    session.add(client)
    session.flush()
    return client


def get_client(session: Session, client_id: str) -> Client | None:
    """An active client, or None."""
    return session.scalars(
        select(Client).where(Client.client_id == client_id, Client.is_active.is_(True))
    ).first()


def list_clients(session: Session) -> list[Client]:
    return list(
        session.scalars(select(Client).where(Client.is_active.is_(True)).order_by(Client.name))
    )


def client_root(client: Client) -> Path:
    return Path(client.inbox_path)


def browse(client: Client, relative: str = "") -> list[str]:
    """Folder names directly inside `relative` (for the Run screen's folder picker)."""
    folder = resolve_inside(client_root(client), relative)
    if not folder.is_dir():
        raise InboxError(f"folder not found: {relative or '(client folder)'}")
    return list_subfolders(folder)
