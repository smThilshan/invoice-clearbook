"""Missan's SharePoint, read only (decision 83): the sign-in, keeping it working, and browsing.

One sign-in for the whole Workbench, made by an admin with their own Microsoft account: the app
then sees exactly the sites and folders that account sees, and can only read them (the scopes in
`adapters.sharepoint.auth`, and a Graph client that only sends GET). Every Workbench user browses
and runs through it.

- The sign-in is started with a signed, short-lived `state` naming the Workbench page to return
  to and the user who started it: a callback that was not started here (or is too old) is
  refused, and the return can only be one of our pages.
- Tokens are encrypted before they reach the database; the access token is renewed shortly
  before it expires, under a row lock (Microsoft replaces the refresh token at each renewal, so
  two renewals at once would lose one).
"""

import hmac
import logging
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import PurePosixPath

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.sharepoint.auth import (
    MicrosoftAuth,
    MicrosoftTokens,
    SharePointAccessRevoked,
    SharePointAuthError,
)
from app.adapters.sharepoint.graph import Account, GraphClient, GraphError, Item, Library, Site
from app.core import token_crypto
from app.core.config import Settings
from app.models import SharePointConnection
from app.pipeline.folders import folder_date_from_path
from app.services.places import INVOICE_EXTENSIONS

logger = logging.getLogger(__name__)

STATE_SECONDS = 600  # a sign-in must be finished within 10 minutes
BACK_PAGES = ("clients", "run")  # Workbench pages a sign-in may return to
RENEW_BEFORE = timedelta(minutes=2)
_STATE_KIND = "sharepoint"  # an accounting sign-in's state can never pass for this one


class SharePointError(Exception):
    """SharePoint cannot be used for this; the message says why, without secrets."""


class SharePointReconnect(SharePointError):  # noqa: N818 - a state, read as a sentence
    """The stored sign-in no longer works: an admin connects SharePoint again."""


@dataclass(frozen=True, slots=True)
class Microsoft:
    """How Microsoft is reached (replaced by a fake in tests)."""

    auth: Callable[[Settings], MicrosoftAuth]
    graph: Callable[[str], GraphClient]


MICROSOFT = Microsoft(auth=MicrosoftAuth, graph=GraphClient)


@dataclass(frozen=True, slots=True)
class Status:
    configured: bool  # the app's keys are in .env
    connection: SharePointConnection | None


@dataclass(frozen=True, slots=True)
class Folder:
    item: Item
    date: date | None  # the date the folder's name (with those above it) stands for


@dataclass(frozen=True, slots=True)
class FolderView:
    folder: Folder
    folders: list[Folder]  # directly inside, by name
    invoice_files: int  # PDF / image files directly inside
    other_files: int  # anything else directly inside (left out of a run)


def is_invoice(item: Item) -> bool:
    """What a run takes: PDFs and photos, not hidden or Office lock files."""
    return (
        not item.is_folder
        and not hidden(item.name)
        and PurePosixPath(item.name).suffix.lower() in INVOICE_EXTENSIONS
    )


def hidden(name: str) -> bool:
    return name.startswith((".", "~$"))


def status(session: Session, settings: Settings, microsoft: Microsoft = MICROSOFT) -> Status:
    return Status(microsoft.auth(settings).configured(), _connection(session))


def start(
    settings: Settings, user_email: str, back: str = "clients", microsoft: Microsoft = MICROSOFT
) -> str:
    """Microsoft's sign-in address; afterwards the user returns to `back`."""
    if back not in BACK_PAGES:
        raise SharePointError(f"cannot return to {back!r}")
    auth = microsoft.auth(settings)
    if not auth.configured():
        raise SharePointError("SharePoint is not set up: its app keys go in .env")
    body = "|".join(
        (_STATE_KIND, back, str(int(time.time())), secrets.token_urlsafe(8), user_email)
    )
    return auth.authorize_url(f"{body}.{_sign(settings, body)}")


def return_page(settings: Settings, state: str) -> str:
    """The page a sign-in returns to when it did not complete."""
    try:
        return _from_state(settings, state)[0]
    except SharePointError:
        return BACK_PAGES[0]


def finish(
    session: Session, settings: Settings, state: str, code: str, microsoft: Microsoft = MICROSOFT
) -> tuple[str, Account]:
    """Microsoft sent the user back: exchange the code and keep the sign-in (replacing any
    earlier one). Returns the page to go back to, and whose account it is. The caller commits."""
    back, user_email = _from_state(settings, state)
    try:
        tokens = microsoft.auth(settings).exchange(code)
        account = microsoft.graph(tokens.access_token).me()
    except (SharePointAuthError, GraphError) as exc:
        raise SharePointError(str(exc)) from exc
    if not tokens.refresh_token:
        raise SharePointError("Microsoft gave no lasting access (offline_access): connect again")
    for old in session.scalars(select(SharePointConnection)):
        session.delete(old)
    session.flush()
    connection = SharePointConnection(
        account_name=account.name or account.email,
        account_email=account.email,
        connected_by=user_email,
    )
    _store(settings, connection, tokens)
    session.add(connection)
    session.flush()
    return back, account


def disconnect(session: Session) -> None:
    """Forget the sign-in here. (Microsoft has no way for an app to revoke it; the account owner
    can remove the app at myapps.microsoft.com.) The caller commits."""
    for connection in session.scalars(select(SharePointConnection)):
        session.delete(connection)
    session.flush()


def call[T](
    session: Session,
    settings: Settings,
    use: Callable[[GraphClient], T],
    microsoft: Microsoft = MICROSOFT,
) -> T:
    """Run `use` with a working Graph client; a token Graph refuses is renewed once."""
    for attempt in (1, 2):
        graph = microsoft.graph(_access_token(session, settings, microsoft))
        try:
            return use(graph)
        except GraphError as exc:
            if exc.access and attempt == 1:
                _expire(session)
                continue
            raise SharePointError(str(exc)) from exc
    raise AssertionError("unreachable")  # pragma: no cover


def sites(session: Session, settings: Settings, microsoft: Microsoft = MICROSOFT) -> list[Site]:
    return call(session, settings, lambda graph: graph.sites(), microsoft)


def libraries(
    session: Session, settings: Settings, site_id: str, microsoft: Microsoft = MICROSOFT
) -> list[Library]:
    return call(session, settings, lambda graph: graph.libraries(site_id), microsoft)


def folder(
    session: Session,
    settings: Settings,
    drive_id: str,
    item_id: str,
    microsoft: Microsoft = MICROSOFT,
) -> FolderView:
    """What is inside a folder, for the Run screen."""

    def look(graph: GraphClient) -> tuple[Item, list[Item]]:
        return graph.item(drive_id, item_id), graph.children(drive_id, item_id)

    item, inside = call(session, settings, look, microsoft)
    if not item.is_folder:
        raise SharePointError(f"{item.name} is a file, not a folder")
    today = settings.business_today()
    files = [i for i in inside if not i.is_folder]
    invoices = sum(1 for i in files if is_invoice(i))
    return FolderView(
        folder=Folder(item, folder_date_from_path(item.path, today=today)),
        folders=[
            Folder(i, folder_date_from_path(i.path, today=today))
            for i in sorted(inside, key=lambda i: i.name.lower())
            if i.is_folder and not hidden(i.name)
        ],
        invoice_files=invoices,
        other_files=len(files) - invoices,
    )


def linked_folder(
    session: Session, settings: Settings, url: str, microsoft: Microsoft = MICROSOFT
) -> Item:
    """The folder a pasted SharePoint address leads to."""
    item = call(session, settings, lambda graph: graph.shared_item(url), microsoft)
    if not item.is_folder:
        raise SharePointError("this link is to a file: paste the link of the folder it is in")
    return item


def _access_token(session: Session, settings: Settings, microsoft: Microsoft) -> str:
    """A working access token. The row is locked only to renew it (about once an hour), so a
    long download never makes other users wait."""
    connection = _usable(_connection(session))
    if connection.expires_at - datetime.now(UTC) > RENEW_BEFORE:
        return _read(settings, connection, connection.access_token)
    connection = _usable(_connection(session, for_update=True))  # renewed meanwhile?
    if connection.expires_at - datetime.now(UTC) > RENEW_BEFORE:
        return _read(settings, connection, connection.access_token)
    refresh_token = _read(settings, connection, connection.refresh_token)
    try:
        tokens = microsoft.auth(settings).refresh(refresh_token)
    except SharePointAccessRevoked as exc:
        logger.warning("SharePoint renewal refused: %s", exc)  # Microsoft's code, no token
        raise _mark(connection, "Microsoft no longer accepts the SharePoint sign-in") from exc
    except SharePointAuthError as exc:
        raise SharePointError(f"SharePoint access could not be renewed ({exc})") from exc
    if not tokens.refresh_token:  # Microsoft kept the old one
        tokens = MicrosoftTokens(tokens.access_token, refresh_token, tokens.expires_at)
    _store(settings, connection, tokens)
    session.flush()
    return tokens.access_token


def _usable(connection: SharePointConnection | None) -> SharePointConnection:
    if connection is None:
        raise SharePointError(
            "SharePoint is not connected: an admin connects it on the Companies page"
        )
    if connection.needs_reconnect:
        raise _reconnect(connection.reconnect_reason or "The SharePoint sign-in stopped working")
    return connection


def _read(settings: Settings, connection: SharePointConnection, stored: str) -> str:
    try:
        return token_crypto.decrypt(settings, stored)
    except token_crypto.TokenKeyError as exc:  # only a new sign-in helps
        raise _mark(connection, str(exc)) from exc


def _mark(connection: SharePointConnection, reason: str) -> SharePointReconnect:
    connection.needs_reconnect, connection.reconnect_reason = True, reason
    logger.warning("SharePoint sign-in needs to be made again")
    return _reconnect(reason)


def _reconnect(reason: str) -> SharePointReconnect:
    return SharePointReconnect(f"{reason}: an admin connects SharePoint again")


def _expire(session: Session) -> None:
    connection = _connection(session, for_update=True)
    if connection is not None:
        connection.expires_at = datetime.now(UTC)
        session.flush()


def _connection(session: Session, *, for_update: bool = False) -> SharePointConnection | None:
    query = select(SharePointConnection).order_by(SharePointConnection.created_at.desc())
    if for_update:  # and read it again: another worker may have just renewed it
        query = query.with_for_update().execution_options(populate_existing=True)
    return session.scalars(query).first()


def _store(settings: Settings, connection: SharePointConnection, tokens: MicrosoftTokens) -> None:
    try:
        connection.access_token = token_crypto.encrypt(settings, tokens.access_token)
        connection.refresh_token = token_crypto.encrypt(settings, tokens.refresh_token)
    except token_crypto.TokenKeyError as exc:
        raise SharePointError(str(exc)) from exc
    connection.expires_at = tokens.expires_at
    connection.needs_reconnect, connection.reconnect_reason = False, None


def _sign(settings: Settings, body: str) -> str:
    try:
        return token_crypto.sign(settings, body)
    except token_crypto.TokenKeyError as exc:
        raise SharePointError(str(exc)) from exc


def _from_state(settings: Settings, state: str) -> tuple[str, str]:
    """(page to return to, user who started it) from a state this app signed."""
    body, _, signature = state.rpartition(".")
    parts = body.split("|", 4)
    if (
        len(parts) != 5
        or parts[0] != _STATE_KIND
        or not hmac.compare_digest(signature, _sign(settings, body))
    ):
        raise SharePointError("this sign-in was not started from the Workbench")
    _, back, issued, _, user_email = parts
    if not issued.isdigit() or time.time() - int(issued) > STATE_SECONDS:
        raise SharePointError("this sign-in took too long: start it again")
    if back not in BACK_PAGES:
        raise SharePointError("this sign-in was not started from the Workbench")
    return back, user_email
