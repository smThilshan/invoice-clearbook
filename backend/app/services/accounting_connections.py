"""A client's connection to its accounting system (task 5.3, decision 38): connect, keep the
access working, disconnect. Xero first; the provider-specific calls stay in the adapter.

- The sign-in is started with a signed, short-lived `state` naming the client: Xero sends it
  back, so a callback that was not started here (or is too old) is refused.
- Tokens are encrypted with settings.token_key before they reach the database.
- The access token lasts 30 minutes: it is renewed shortly before it expires, under a row lock
  so two workers cannot renew the same refresh token at once.
"""

import hashlib
import hmac
import secrets
import time
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.accounting.xero.auth import Tokens, XeroAuth, XeroAuthError
from app.core.config import Settings
from app.domain.enums import AccountingProvider
from app.models import AccountingConnection, Client

STATE_SECONDS = 600  # a sign-in must be finished within 10 minutes
RENEW_BEFORE = timedelta(minutes=2)


class AccountingConnectionError(Exception):
    """The connection cannot be made, used or removed; the message says why, without secrets."""


@dataclass(frozen=True, slots=True)
class Connected:
    client_id: str
    tenant_name: str


def start_xero(settings: Settings, auth: XeroAuth, client: Client) -> str:
    """The Xero sign-in address for this client."""
    if not auth.configured():
        raise AccountingConnectionError(
            "Xero is not set up: XERO_CLIENT_ID and XERO_CLIENT_SECRET in .env"
        )
    _cipher(settings)  # fail now, not after the user signed in, when TOKEN_KEY is missing
    return auth.authorize_url(_state(settings, client.client_id))


def finish_xero(
    session: Session, settings: Settings, auth: XeroAuth, state: str, code: str
) -> Connected:
    """Xero sent the user back: exchange the code, pick the organisation they approved and
    store the connection. The caller commits."""
    client_id = _client_from_state(settings, state)
    client = session.scalars(select(Client).where(Client.client_id == client_id)).first()
    if client is None:
        raise AccountingConnectionError("this client no longer exists")
    try:
        tokens = auth.exchange(code)
        tenants = auth.tenants(tokens.access_token)
    except XeroAuthError as exc:
        raise AccountingConnectionError(str(exc)) from exc
    approved = [
        t for t in tenants if tokens.auth_event_id and t.auth_event_id == tokens.auth_event_id
    ]
    choice = (approved or tenants)[:1]
    if not choice:
        raise AccountingConnectionError(
            "no Xero organisation was approved: choose one when signing in"
        )
    tenant = choice[0]

    connection = _connection(session, client_id) or AccountingConnection(
        client_id=client_id, provider=AccountingProvider.XERO
    )
    connection.tenant_id, connection.tenant_name = tenant.tenant_id, tenant.name
    connection.connection_id = tenant.connection_id
    _store(settings, connection, tokens)
    session.add(connection)
    session.flush()
    return Connected(client_id, tenant.name)


def connection_of(session: Session, client_id: str) -> AccountingConnection | None:
    return _connection(session, client_id)


def access_token(session: Session, settings: Settings, auth: XeroAuth, client_id: str) -> str:
    """A working access token for the client's Xero organisation, renewed when needed."""
    connection = _connection(session, client_id, for_update=True)
    if connection is None:
        raise AccountingConnectionError("this client is not connected to Xero")
    cipher = _cipher(settings)
    if connection.expires_at - datetime.now(UTC) > RENEW_BEFORE:
        return _decrypt(cipher, connection.access_token)
    try:
        tokens = auth.refresh(_decrypt(cipher, connection.refresh_token))
    except XeroAuthError as exc:
        raise AccountingConnectionError(f"Xero access expired, connect again ({exc})") from exc
    _store(settings, connection, tokens)
    session.flush()
    return tokens.access_token


def disconnect(session: Session, settings: Settings, auth: XeroAuth, client_id: str) -> None:
    """Remove the connection here, and in Xero when it can still be reached. The caller commits."""
    connection = _connection(session, client_id, for_update=True)
    if connection is None:
        return
    if connection.connection_id:
        # Already revoked or expired in Xero: removing it here is what matters.
        with suppress(AccountingConnectionError, XeroAuthError):
            token = access_token(session, settings, auth, client_id)
            auth.disconnect(token, connection.connection_id)
    session.delete(connection)
    session.flush()


def _connection(
    session: Session, client_id: str, *, for_update: bool = False
) -> AccountingConnection | None:
    query = select(AccountingConnection).where(
        AccountingConnection.client_id == client_id,
        AccountingConnection.provider == AccountingProvider.XERO,
    )
    return session.scalars(query.with_for_update() if for_update else query).first()


def _store(settings: Settings, connection: AccountingConnection, tokens: Tokens) -> None:
    cipher = _cipher(settings)
    connection.access_token = cipher.encrypt(tokens.access_token.encode()).decode()
    connection.refresh_token = cipher.encrypt(tokens.refresh_token.encode()).decode()
    connection.expires_at = tokens.expires_at
    connection.scopes = tokens.scopes


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
            "stored Xero access cannot be read (TOKEN_KEY changed?): connect again"
        ) from exc


def _state(settings: Settings, client_id: str) -> str:
    body = f"{client_id}.{int(time.time())}.{secrets.token_urlsafe(8)}"
    return f"{body}.{_sign(settings, body)}"


def _client_from_state(settings: Settings, state: str) -> str:
    body, _, signature = state.rpartition(".")
    parts = body.split(".")
    if len(parts) != 3 or not hmac.compare_digest(signature, _sign(settings, body)):
        raise AccountingConnectionError("this sign-in was not started from the Workbench")
    client_id, issued, _ = parts
    if not issued.isdigit() or time.time() - int(issued) > STATE_SECONDS:
        raise AccountingConnectionError("this sign-in took too long: start it again")
    return client_id


def _sign(settings: Settings, body: str) -> str:
    key = settings.token_key.get_secret_value() or settings.xero_client_secret.get_secret_value()
    return hmac.new(key.encode(), body.encode(), hashlib.sha256).hexdigest()[:32]
