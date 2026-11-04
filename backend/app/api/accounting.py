"""A client's accounting system (tasks 5.1, 5.3, 5.4; decision 43): connect, status, sync, send.

The browser follows two addresses per provider, named by its slug in the registry: the
Workbench links to /<slug>/connect (e.g. /xero/connect), the provider's sign-in sends the user
back to /<slug>/callback (the address registered with the provider), and that sends them back
to the Workbench with the outcome. No login yet: the API listens on localhost only.
"""

import logging
from collections.abc import Callable
from dataclasses import asdict
from datetime import datetime
from typing import Annotated, Literal
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.adapters.accounting.base import MasterDataError
from app.adapters.accounting.registry import PROVIDERS, Registry, by_slug
from app.api.auth import require_admin
from app.core.config import Settings, get_settings
from app.core.db import get_session
from app.domain.enums import AccountingProvider, TaxType
from app.models import Client, LedgerAccount
from app.services import posting
from app.services.accounting_connections import (
    AccountingConnectionError,
    connection_of,
    disconnect,
    finish,
    return_page,
    start,
)
from app.services.accounting_sync import sync
from app.services.clients import get_client
from app.services.master_data import Counts

logger = logging.getLogger(__name__)
router = APIRouter(tags=["accounting system"])
# The provider sends the browser back here from its own site: our SameSite cookie does not come
# along, so this one route is public; the signed `state` proves the sign-in started here.
callback_router = APIRouter(tags=["accounting system"])

SessionDep = Annotated[Session, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


def get_registry() -> Registry:
    """The accounting systems that can be connected (replaced in tests)."""
    return PROVIDERS


RegistryDep = Annotated[Registry, Depends(get_registry)]


class ProviderOut(BaseModel):
    key: AccountingProvider
    name: str  # "Xero"
    configured: bool  # its app keys are in .env
    connect_url: str  # the browser goes here to connect a client (add ?client_id=)


class PostingOut(BaseModel):
    """Where the client's approved documents are (see services/posting.PostingSummary)."""

    ready: int
    retrying: int
    retry_at: datetime | None
    retry_reason: str | None
    not_yet: dict[str, int]  # document type -> count, e.g. {"RECEIPT": 2}
    sent_back: int
    posted: int
    paused: int  # waiting for someone to connect again (decision 68)
    paused_reason: str | None
    failed: int  # gave up: Try again or Send back on the document (decision 69)
    failed_reason: str | None


class AccountingStatus(BaseModel):
    providers: list[ProviderOut]  # what this client could be connected to
    configured: bool  # at least one provider has its app keys in .env
    connected: bool
    needs_reconnect: bool  # the access was withdrawn: sending is paused until connected again
    reconnect_reason: str | None
    provider: AccountingProvider | None  # the connected one
    provider_name: str | None  # "Xero", for the screen
    organisation: str | None
    connected_at: datetime | None
    bills_where: str | None  # where sent bills are found in the provider's own screens
    master_data_from: str  # the provider once synced, else "MANUAL" (CSV import)
    accounts: int  # active accounts coding can use, from `master_data_from` (decision 79)
    waiting: int  # approved documents not posted yet
    posting: PostingOut


class SyncCounts(BaseModel):
    created: int
    updated: int
    unchanged: int


class TaxMappingOut(BaseModel):
    code: str
    name: str
    tax_type: TaxType | None  # our VAT type; None = not used for purchases


class SyncOut(BaseModel):
    accounts: SyncCounts
    tax_rates: SyncCounts
    contacts: SyncCounts
    deactivated: int
    tax_mapping: list[TaxMappingOut]


@router.get("/{slug}/connect", dependencies=[Depends(require_admin)])
def connect(
    slug: str,
    client_id: str,
    session: SessionDep,
    settings: SettingsDep,
    registry: RegistryDep,
    back: Literal["clients", "run"] = "clients",
) -> RedirectResponse:
    """Start the provider's sign-in for this client; afterwards the user returns to `back`, the
    Workbench page they started from (decision 79)."""
    entry = by_slug(registry, slug)
    if entry is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown accounting system")
    client = get_client(session, client_id)
    if client is None:
        return _back(settings, back, error="client not found")
    try:
        url = start(settings, entry, client, back)
    except AccountingConnectionError as exc:
        return _back(settings, back, client=client_id, error=str(exc))
    return RedirectResponse(url, status.HTTP_307_TEMPORARY_REDIRECT)


def get_enqueue_sweep() -> Callable[[str], object]:
    from app.workers.tasks import send_posting  # imported lazily: the API does not need a worker

    return lambda client_id: send_posting.delay(None, client_id)


@callback_router.get("/{slug}/callback")
def callback(
    slug: str,
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    registry: RegistryDep,
    enqueue: Annotated[Callable[[str], object], Depends(get_enqueue_sweep)],
    state: str = "",
    code: Annotated[str, Query()] = "",
    error: str | None = None,
) -> RedirectResponse:
    """The provider sends the user back here; they are sent on to the Workbench. Bills that
    waited for this (a paused connection, decision 68) are sent at once."""
    entry = by_slug(registry, slug)
    if entry is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown accounting system")
    back = return_page(settings, state)
    if error or not code:
        return _back(
            settings, back, error=f"{entry.name} sign-in was not completed ({error or 'no code'})"
        )
    try:
        connected = finish(session, settings, registry, state, code, dict(request.query_params))
        session.commit()
    except AccountingConnectionError as exc:
        session.rollback()
        return _back(settings, back, error=str(exc))
    try:
        enqueue(connected.client_id)
    except Exception:  # connected and saved: "Send waiting bills" or the worker's start sends them
        logger.warning("client %s: could not ask the worker to send now", connected.client_id)
    return _back(
        settings,
        connected.back,
        client=connected.client_id,
        connected=connected.organisation,
        system=connected.provider_name,
    )


@router.get("/clients/{client_id}/accounting")
def accounting_status(
    client_id: str, session: SessionDep, settings: SettingsDep, registry: RegistryDep
) -> AccountingStatus:
    client = _found(session, client_id)
    connection = connection_of(session, client_id)
    entry = registry.get(connection.provider) if connection else None
    providers = [
        ProviderOut(
            key=e.provider,
            name=e.name,
            configured=e.connector(settings).configured(),
            connect_url=f"/{e.slug}/connect",
        )
        for e in registry.values()
    ]
    return AccountingStatus(
        providers=providers,
        configured=any(p.configured for p in providers),
        connected=connection is not None,
        needs_reconnect=bool(connection and connection.needs_reconnect),
        reconnect_reason=connection.reconnect_reason if connection else None,
        provider=connection.provider if connection else None,
        provider_name=entry.name if entry else None,
        organisation=connection.tenant_name if connection else None,
        connected_at=connection.created_at if connection else None,
        bills_where=entry.bills_where if entry else None,
        master_data_from=client.accounting_provider.value,
        accounts=session.scalar(
            select(func.count()).where(
                LedgerAccount.client_id == client_id,
                LedgerAccount.provider == client.accounting_provider,
                LedgerAccount.is_active.is_(True),
            )
        )
        or 0,
        waiting=posting.waiting(session, client_id),
        posting=PostingOut(**asdict(posting.summary(session, client_id))),
    )


@router.post("/clients/{client_id}/accounting/disconnect", dependencies=[Depends(require_admin)])
def disconnect_accounting(
    client_id: str, session: SessionDep, settings: SettingsDep, registry: RegistryDep
) -> AccountingStatus:
    _found(session, client_id)
    disconnect(session, settings, registry, client_id)
    session.commit()
    return accounting_status(client_id, session, settings, registry)


@router.post("/clients/{client_id}/accounting/sync", dependencies=[Depends(require_admin)])
def sync_accounting(
    client_id: str, session: SessionDep, settings: SettingsDep, registry: RegistryDep
) -> SyncOut:
    """Read accounts, tax rates and contacts from the connected accounting system. All or
    nothing; afterwards coding and checks use its master data."""
    client = _found(session, client_id)
    try:
        report = sync(session, settings, registry, client)
    except AccountingConnectionError as exc:
        session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except MasterDataError as exc:
        session.rollback()
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, {"problems": exc.problems}
        ) from exc
    session.commit()
    return SyncOut(
        accounts=_counts(report.accounts),
        tax_rates=_counts(report.tax_rates),
        contacts=_counts(report.contacts),
        deactivated=report.deactivated,
        tax_mapping=[
            TaxMappingOut(code=m.code, name=m.name, tax_type=m.tax_type) for m in report.tax_mapping
        ],
    )


@router.post("/clients/{client_id}/accounting/send")
def send_waiting(
    client_id: str,
    session: SessionDep,
    settings: SettingsDep,
    registry: RegistryDep,
    enqueue: Annotated[Callable[[str], object], Depends(get_enqueue_sweep)],
) -> AccountingStatus:
    """Ask the posting worker to send this client's approved documents now (task 5.1)."""
    status_now = accounting_status(client_id, session, settings, registry)
    if status_now.needs_reconnect:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"connect {status_now.provider_name} again first"
        )
    if not status_now.connected or status_now.master_data_from != status_now.provider:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "connect and sync the accounting system first"
        )
    enqueue(client_id)
    return status_now


def _found(session: Session, client_id: str) -> Client:
    client = get_client(session, client_id)
    if client is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "client not found")
    return client


def _counts(counts: Counts) -> SyncCounts:
    return SyncCounts(created=counts.created, updated=counts.updated, unchanged=counts.unchanged)


def _back(settings: Settings, page: str = "clients", **outcome: str) -> RedirectResponse:
    """Back to a Workbench page (only ever one of BACK_PAGES), saying what happened."""
    query = urlencode({key: value for key, value in outcome.items() if value})
    return RedirectResponse(f"{settings.workbench_url}/{page}?{query}", status.HTTP_303_SEE_OTHER)
