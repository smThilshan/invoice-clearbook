"""A client's connection to its accounting system (task 5.3, decisions 38, 43): connect, keep
the access working, disconnect. Any provider in the registry; its own calls stay in its adapter.

- The sign-in is started with a signed, short-lived `state` naming the client, the provider and
  the Workbench page to return to (decision 79): the provider sends it back, so a callback that
  was not started here (or is too old) is refused, and the return can only be one of our pages.
- Tokens are encrypted with settings.token_key before they reach the database.
- An access token is renewed shortly before it expires, under a row lock so two workers cannot
  renew the same refresh token at once.
"""

import hashlib
import hmac
import logging
import secrets
import time
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.accounting.base import AccessRevoked, ConnectorError, Tokens
from app.adapters.accounting.registry import ProviderEntry, Registry, by_slug
from app.core.config import Settings
from app.models import AccountingConnection, Client

logger = logging.getLogger(__name__)

STATE_SECONDS = 600  # a sign-in must be finished within 10 minutes
# Workbench pages a sign-in may return to (decision 79): never an address from the request.
BACK_PAGES = ("clients", "run")
RENEW_BEFORE = timedelta(minutes=2)


class AccountingConnectionError(Exception):
    """The connection cannot be made, used or removed; the message says why, without secrets."""


class ReconnectNeeded(AccountingConnectionError):  # noqa: N818 - a state, read as a sentence
    """The stored access is gone for good (decision 68): the connection is marked, and nothing
    is sent for this client until someone connects again."""


@dataclass(frozen=True, slots=True)
class Connected:
    client_id: str
    organisation: str
    provider_name: str
    back: str  # the Workbench page the sign-in was started from


def start(settings: Settings, entry: ProviderEntry, client: Client, back: str = "clients") -> str:
    """The provider's sign-in address for this client; afterwards the user returns to `back`."""
    if back not in BACK_PAGES:
        raise AccountingConnectionError(f"cannot return to {back!r}")
    if not entry.connector(settings).configured():
        raise AccountingConnectionError(f"{entry.name} is not set up: its app keys go in .env")
    _cipher(settings)  # fail now, not after the user signed in, when TOKEN_KEY is missing
    state = _state(settings, client.client_id, entry.slug, back)
    return entry.connector(settings).authorize_url(state)


def finish(
    session: Session,
    settings: Settings,
    registry: Registry,
    state: str,
    code: str,
    returned: Mapping[str, str] | None = None,
) -> Connected:
    """The provider sent the user back (`returned`: everything it sent): exchange the code, pick
    the organisation they approved and store the connection. The caller commits."""
    client_id, slug, back = _from_state(settings, state)
    entry = by_slug(registry, slug)
    if entry is None:
        raise AccountingConnectionError("this sign-in names an accounting system we do not know")
    client = session.scalars(select(Client).where(Client.client_id == client_id)).first()
    if client is None:
        raise AccountingConnectionError("this client no longer exists")
    connector = entry.connector(settings)
    try:
        tokens = connector.exchange(code, returned)
        organisations = connector.organisations(tokens.access_token, tokens.region)
    except ConnectorError as exc:
        raise AccountingConnectionError(str(exc)) from exc
    approved = [
        o for o in organisations if tokens.approval_id and o.approval_id == tokens.approval_id
    ]
    if not (approved or organisations):
        raise AccountingConnectionError(
            f"no {entry.name} organisation was approved: choose one when signing in"
        )
    if not approved and len(organisations) > 1:  # never guess which company this client is
        raise AccountingConnectionError(
            f"this {entry.name} login opens {len(organisations)} organisations; choosing one "
            "is not available yet: sign in with a login that opens only this client's"
        )
    organisation = (approved or organisations)[0]

    # One accounting system per client: connecting another replaces the old connection.
    for old in _connections(session, client_id):
        if old.provider is not entry.provider:
            session.delete(old)
    connection = _connection(session, client_id) or AccountingConnection(
        client_id=client_id, provider=entry.provider
    )
    connection.tenant_id, connection.tenant_name = organisation.organisation_id, organisation.name
    connection.connection_id = organisation.connection_id
    connection.needs_reconnect, connection.reconnect_reason = False, None
    _store(settings, connection, tokens)
    session.add(connection)
    session.flush()
    return Connected(client_id, organisation.name, entry.name, back)


def return_page(settings: Settings, state: str) -> str:
    """The page a sign-in returns to when it did not complete: the one in its state if this app
    signed it, else the Clients page."""
    try:
        return _from_state(settings, state)[2]
    except AccountingConnectionError:
        return BACK_PAGES[0]


def connection_of(session: Session, client_id: str) -> AccountingConnection | None:
    return _connection(session, client_id)


def access_token(session: Session, settings: Settings, registry: Registry, client_id: str) -> str:
    """A working access token for the client's organisation, renewed when needed. Raises
    ReconnectNeeded (and marks the connection) when only signing in again can help; any other
    AccountingConnectionError may pass by itself."""
    connection = _connection(session, client_id, for_update=True)
    if connection is None:
        raise AccountingConnectionError("this client is not connected to an accounting system")
    entry = registry[connection.provider]
    if connection.needs_reconnect:
        raise ReconnectNeeded(connection.reconnect_reason or f"connect {entry.name} again")
    cipher = _cipher(settings)
    try:
        if connection.expires_at - datetime.now(UTC) > RENEW_BEFORE:
            return _decrypt(cipher, connection.access_token)
        tokens = entry.connector(settings).refresh(
            _decrypt(cipher, connection.refresh_token), connection.region
        )
    except AccessRevoked as exc:
        raise _mark(connection, f"{entry.name} access was withdrawn ({exc})") from exc
    except ConnectorError as exc:
        raise AccountingConnectionError(f"{entry.name} could not renew access ({exc})") from exc
    except AccountingConnectionError as exc:  # stored tokens unreadable: only a new sign-in helps
        raise _mark(connection, str(exc)) from exc
    _store(settings, connection, tokens)
    session.flush()
    return tokens.access_token


def mark_reconnect_needed(session: Session, client_id: str, reason: str) -> None:
    """The provider refused the access while it was used (e.g. a permission missing)."""
    connection = _connection(session, client_id, for_update=True)
    if connection is not None:
        _mark(connection, reason)
        session.flush()


def expire_access(session: Session, client_id: str) -> None:
    """The provider refused an access token we thought valid: renew it at the next use."""
    connection = _connection(session, client_id, for_update=True)
    if connection is not None:
        connection.expires_at = datetime.now(UTC)
        session.flush()


def _mark(connection: AccountingConnection, reason: str) -> ReconnectNeeded:
    connection.needs_reconnect, connection.reconnect_reason = True, reason
    logger.warning("client %s: accounting connection needs a new sign-in", connection.client_id)
    return ReconnectNeeded(reason)


def disconnect(session: Session, settings: Settings, registry: Registry, client_id: str) -> None:
    """Remove the connection here, and in the provider when it can still be reached. The caller
    commits."""
    connection = _connection(session, client_id, for_update=True)
    if connection is None:
        return
    # Already revoked or expired there: removing it here is what matters.
    with suppress(AccountingConnectionError, ConnectorError):
        token = access_token(session, settings, registry, client_id)
        registry[connection.provider].connector(settings).disconnect(
            token,
            connection.connection_id,
            refresh_token=_decrypt(_cipher(settings), connection.refresh_token),
            region=connection.region,
        )
    session.delete(connection)
    session.flush()


def _connections(session: Session, client_id: str) -> list[AccountingConnection]:
    query = select(AccountingConnection).where(AccountingConnection.client_id == client_id)
    return list(session.scalars(query))


def _connection(
    session: Session, client_id: str, *, for_update: bool = False
) -> AccountingConnection | None:
    query = (
        select(AccountingConnection)
        .where(AccountingConnection.client_id == client_id)
        .order_by(AccountingConnection.created_at.desc())
    )
    return session.scalars(query.with_for_update() if for_update else query).first()


def _store(settings: Settings, connection: AccountingConnection, tokens: Tokens) -> None:
    cipher = _cipher(settings)
    connection.access_token = cipher.encrypt(tokens.access_token.encode()).decode()
    connection.refresh_token = cipher.encrypt(tokens.refresh_token.encode()).decode()
    connection.expires_at = tokens.expires_at
    connection.scopes = tokens.scopes
    connection.region = tokens.region


def _cipher(settings: Settings) -> Fernet:
    key = settings.token_key.get_secret_value()
    if not key:
        raise AccountingConnectionError(
            "TOKEN_KEY is not set in .env: create one with `make secret-key`"
        )
    try:
        return Fernet(key)
    except ValueError as exc:
        raise AccountingConnectionError(
            "TOKEN_KEY in .env is not a valid key: `make secret-key`"
        ) from exc


def _decrypt(cipher: Fernet, value: str) -> str:
    try:
        return cipher.decrypt(value.encode()).decode()
    except InvalidToken as exc:
        raise AccountingConnectionError(
            "stored access cannot be read (TOKEN_KEY changed?): connect again"
        ) from exc


def _state(settings: Settings, client_id: str, slug: str, back: str = "clients") -> str:
    body = f"{client_id}.{slug}.{back}.{int(time.time())}.{secrets.token_urlsafe(8)}"
    return f"{body}.{_sign(settings, body)}"


def _from_state(settings: Settings, state: str) -> tuple[str, str, str]:
    """(client id, provider slug, page to return to) from a state this app signed."""
    body, _, signature = state.rpartition(".")
    parts = body.split(".")
    if len(parts) != 5 or not hmac.compare_digest(signature, _sign(settings, body)):
        raise AccountingConnectionError("this sign-in was not started from the Workbench")
    client_id, slug, back, issued, _ = parts
    if not issued.isdigit() or time.time() - int(issued) > STATE_SECONDS:
        raise AccountingConnectionError("this sign-in took too long: start it again")
    if back not in BACK_PAGES:
        raise AccountingConnectionError("this sign-in was not started from the Workbench")
    return client_id, slug, back


def _sign(settings: Settings, body: str) -> str:
    _cipher(settings)  # no TOKEN_KEY: refuse, never sign with an empty or borrowed key
    key = settings.token_key.get_secret_value()
    return hmac.new(key.encode(), body.encode(), hashlib.sha256).hexdigest()[:32]
