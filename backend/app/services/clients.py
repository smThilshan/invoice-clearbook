"""Clients (decisions 25, 35): any business whose books are kept, optionally with its own folder."""

import re
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.accounting.registry import Registry, expenses_blocked_by
from app.domain.enums import AccountingProvider, ExpensesSentAs
from app.models import Client, Run
from app.pipeline import coding
from app.pipeline.folders import InboxError, list_subfolders, resolve_inside
from app.services.accounting_connections import connection_of

CLIENT_ID = re.compile(r"[a-z0-9][a-z0-9-]{1,62}[a-z0-9]")  # e.g. "cafe-al-noor"


class ClientError(Exception):
    pass


def add_client(
    session: Session,
    *,
    client_id: str,
    name: str,
    inbox_path: Path | None = None,
    accounting_provider: AccountingProvider = AccountingProvider.MANUAL,
) -> Client:
    """Register a client. Its own folder is optional (decision 35); if given, it must exist
    and be readable now."""
    if not CLIENT_ID.fullmatch(client_id):
        raise ClientError("company id: lowercase letters, digits and dashes, e.g. cafe-al-noor")
    if not name.strip():
        raise ClientError("name is required")
    if inbox_path is not None:
        if not inbox_path.is_absolute():
            raise ClientError("the inbox folder must be an absolute path")
        if not inbox_path.is_dir():
            raise ClientError(f"the inbox folder does not exist or is not reachable: {inbox_path}")
        try:
            list_subfolders(inbox_path)
        except InboxError as exc:
            raise ClientError(f"the inbox folder cannot be read: {exc}") from exc
    if session.scalars(select(Client).where(Client.client_id == client_id)).first():
        raise ClientError(f"company {client_id!r} already exists")

    client = Client(
        client_id=client_id,
        name=name.strip(),
        inbox_path=str(inbox_path) if inbox_path else None,
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


def new_client_id(session: Session, name: str) -> str:
    """A free id made from the name: "Cafe Al Noor" -> "cafe-al-noor" (then -2, -3, ...)."""
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:56].strip("-")
    base = base if len(base) >= 3 else f"client-{base}".strip("-")
    taken = set(session.scalars(select(Client.client_id).where(Client.client_id.like(f"{base}%"))))
    if base not in taken:
        return base
    return next(f"{base}-{n}" for n in range(2, len(taken) + 3) if f"{base}-{n}" not in taken)


def set_expense_sending(
    session: Session,
    registry: Registry,
    client: Client,
    *,
    sent_as: ExpensesSentAs,
    paid_through: str | None,
) -> None:
    """Decision 89: how the client's expenses are sent, and the account they are usually paid
    through (one of its synced payment accounts). Sending them as expenses needs a system that
    takes them and a connection allowed to."""
    if sent_as is ExpensesSentAs.EXPENSE:
        blocked = expenses_blocked(session, registry, client.client_id)
        if blocked:
            raise ClientError(blocked)
    if paid_through is not None:
        chart = coding.load_chart(
            session, client_id=client.client_id, provider=client.accounting_provider
        )
        if paid_through not in {account.code for account in chart.payment_accounts()}:
            raise ClientError(
                "an expense cannot be paid through that account: sync the master data and "
                "choose again"
            )
    client.expenses_sent_as = sent_as
    client.paid_through = paid_through


def expenses_blocked(session: Session, registry: Registry, client_id: str) -> str | None:
    """Why the client's expenses cannot be sent as expenses now; None = they can."""
    connection = connection_of(session, client_id)
    if connection is None:
        return expenses_blocked_by(None, None)
    return expenses_blocked_by(registry[connection.provider], connection.scopes)


def client_root(client: Client) -> Path:
    if not client.inbox_path:
        raise InboxError(f"company {client.client_id!r} has no folder of its own: pick a place")
    return Path(client.inbox_path)


def run_root(run: Run, client: Client) -> Path:
    """Where a run's folder and files are (decision 35); older runs use the client's folder."""
    return Path(run.root_path) if run.root_path else client_root(client)


def browse(client: Client, relative: str = "") -> list[str]:
    """Folder names directly inside `relative` (for the Run screen's folder picker)."""
    folder = resolve_inside(client_root(client), relative)
    if not folder.is_dir():
        raise InboxError(f"folder not found: {relative or '(company folder)'}")
    return list_subfolders(folder)
