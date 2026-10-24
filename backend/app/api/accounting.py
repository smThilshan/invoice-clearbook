"""A client's accounting system (tasks 5.1, 5.3, 5.4; decision 43): connect, status, sync, send.

The browser follows two addresses per provider, named by its slug in the registry: the
Workbench links to /<slug>/connect (e.g. /xero/connect), the provider's sign-in sends the user
back to /<slug>/callback (the address registered with the provider), and that sends them back
to the Workbench with the outcome. No login yet: the API listens on localhost only.
"""

from collections.abc import Callable
from dataclasses import asdict
from datetime import datetime
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.adapters.accounting.base import MasterDataError
from app.adapters.accounting.registry import PROVIDERS, Registry, by_slug
from app.core.config import Settings, get_settings
from app.core.db import get_session
from app.domain.enums import AccountingProvider, TaxType
from app.models import Client
from app.services import posting
from app.services.accounting_connections import (
    AccountingConnectionError,
    connection_of,
    disconnect,
    finish,
    start,
)
from app.services.accounting_sync import sync
from app.services.clients import get_client
from app.services.master_data import Counts

router = APIRouter(tags=["accounting system"])

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


class AccountingStatus(BaseModel):
    providers: list[ProviderOut]  # what this client could be connected to
    configured: bool  # at least one provider has its app keys in .env
    connected: bool
    provider: AccountingProvider | None  # the connected one
    provider_name: str | None  # "Xero", for the screen
    organisation: str | None
    connected_at: datetime | None
    bills_where: str | None  # where sent bills are found in the provider's own screens
    master_data_from: str  # the provider once synced, else "MANUAL" (CSV import)
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


@router.get("/{slug}/connect")
def connect(
    slug: str, client_id: str, session: SessionDep, settings: SettingsDep, registry: RegistryDep
) -> RedirectResponse:
    """Start the provider's sign-in for this client."""
    entry = by_slug(registry, slug)
    if entry is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown accounting system")
    client = get_client(session, client_id)
    if client is None:
        return _back(settings, error="client not found")
    try:
        return RedirectResponse(start(settings, entry, client), status.HTTP_307_TEMPORARY_REDIRECT)
    except AccountingConnectionError as exc:
        return _back(settings, client=client_id, error=str(exc))


@router.get("/{slug}/callback")
def callback(
    slug: str,
    session: SessionDep,
    settings: SettingsDep,
    registry: RegistryDep,
    state: str = "",
    code: Annotated[str, Query()] = "",
    error: str | None = None,
) -> RedirectResponse:
    """The provider sends the user back here; they are sent on to the Workbench."""
    entry = by_slug(registry, slug)
    if entry is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown accounting system")
    if error or not code:
        return _back(
            settings, error=f"{entry.name} sign-in was not completed ({error or 'no code'})"
        )
    try:
        connected = finish(session, settings, registry, state, code)
        session.commit()
    except AccountingConnectionError as exc:
        session.rollback()
        return _back(settings, error=str(exc))
    return _back(
        settings,
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
        provider=connection.provider if connection else None,
        provider_name=entry.name if entry else None,
        organisation=connection.tenant_name if connection else None,
        connected_at=connection.created_at if connection else None,
        bills_where=entry.bills_where if entry else None,
        master_data_from=client.accounting_provider.value,
        waiting=posting.waiting(session, client_id),
        posting=PostingOut(**asdict(posting.summary(session, client_id))),
    )


@router.post("/clients/{client_id}/accounting/disconnect")
def disconnect_accounting(
    client_id: str, session: SessionDep, settings: SettingsDep, registry: RegistryDep
) -> AccountingStatus:
    _found(session, client_id)
    disconnect(session, settings, registry, client_id)
    session.commit()
    return accounting_status(client_id, session, settings, registry)


@router.post("/clients/{client_id}/accounting/sync")
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


def get_enqueue_sweep() -> Callable[[str], object]:
    from app.workers.tasks import send_posting  # imported lazily: the API does not need a worker

    return lambda client_id: send_posting.delay(None, client_id)


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


def _back(settings: Settings, **outcome: str) -> RedirectResponse:
    """Back to the Workbench's Clients page, saying what happened."""
    query = urlencode({key: value for key, value in outcome.items() if value})
    return RedirectResponse(f"{settings.workbench_url}/clients?{query}", status.HTTP_303_SEE_OTHER)
