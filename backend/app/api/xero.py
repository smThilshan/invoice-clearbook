"""Connecting a client to Xero (task 5.3). The browser follows these addresses: the Workbench
links to /xero/connect, Xero's sign-in sends the user back to /xero/callback, and that sends
them back to the Workbench with the outcome. No login yet: the API listens on localhost only.
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

from app.adapters.accounting.base import AccountingSystem, MasterDataError
from app.adapters.accounting.xero.auth import XeroAuth
from app.adapters.accounting.xero.master_data import XeroAccountingSystem
from app.core.config import Settings, get_settings
from app.core.db import get_session
from app.domain.enums import TaxType
from app.services import posting
from app.services.accounting_connections import (
    AccountingConnectionError,
    connection_of,
    disconnect,
    finish_xero,
    start_xero,
)
from app.services.accounting_sync import sync_xero
from app.services.clients import get_client
from app.services.master_data import Counts

router = APIRouter(tags=["accounting connection"])

SessionDep = Annotated[Session, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


def get_xero_auth(settings: SettingsDep) -> XeroAuth:
    return XeroAuth(settings)


XeroDep = Annotated[XeroAuth, Depends(get_xero_auth)]


def get_xero_system() -> Callable[[str, str], AccountingSystem]:
    """Builds the Xero API client for one organisation (replaced in tests)."""
    return XeroAccountingSystem


XeroSystemDep = Annotated[Callable[[str, str], AccountingSystem], Depends(get_xero_system)]


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
    configured: bool  # the Xero app keys are in .env
    connected: bool
    provider: str | None  # "XERO" when connected
    organisation: str | None
    connected_at: datetime | None
    master_data_from: str  # "XERO" once synced, else "MANUAL" (CSV import)
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


@router.get("/xero/connect")
def connect(
    client_id: str, session: SessionDep, settings: SettingsDep, auth: XeroDep
) -> RedirectResponse:
    """Start the Xero sign-in for this client."""
    client = get_client(session, client_id)
    if client is None:
        return _back(settings, error="client not found")
    try:
        return RedirectResponse(
            start_xero(settings, auth, client), status.HTTP_307_TEMPORARY_REDIRECT
        )
    except AccountingConnectionError as exc:
        return _back(settings, client=client_id, error=str(exc))


@router.get("/xero/callback")
def callback(
    session: SessionDep,
    settings: SettingsDep,
    auth: XeroDep,
    state: str = "",
    code: Annotated[str, Query()] = "",
    error: str | None = None,
) -> RedirectResponse:
    """Xero sends the user back here; they are sent on to the Workbench."""
    if error or not code:
        return _back(settings, error=f"Xero sign-in was not completed ({error or 'no code'})")
    try:
        connected = finish_xero(session, settings, auth, state, code)
        session.commit()
    except AccountingConnectionError as exc:
        session.rollback()
        return _back(settings, error=str(exc))
    return _back(settings, client=connected.client_id, connected=connected.tenant_name)


@router.get("/clients/{client_id}/accounting")
def accounting_status(client_id: str, session: SessionDep, auth: XeroDep) -> AccountingStatus:
    client = get_client(session, client_id)
    if client is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "client not found")
    connection = connection_of(session, client_id)
    return AccountingStatus(
        configured=auth.configured(),
        connected=connection is not None,
        provider=connection.provider.value if connection else None,
        organisation=connection.tenant_name if connection else None,
        connected_at=connection.created_at if connection else None,
        master_data_from=client.accounting_provider.value,
        waiting=posting.waiting(session, client_id),
        posting=PostingOut(**asdict(posting.summary(session, client_id))),
    )


def get_enqueue_sweep() -> Callable[[str], object]:
    from app.workers.tasks import send_posting  # imported lazily: the API does not need a worker

    return lambda client_id: send_posting.delay(None, client_id)


@router.post("/clients/{client_id}/accounting/send")
def send_waiting(
    client_id: str,
    session: SessionDep,
    auth: XeroDep,
    enqueue: Annotated[Callable[[str], object], Depends(get_enqueue_sweep)],
) -> AccountingStatus:
    """Ask the posting worker to send this client's approved documents now (task 5.1)."""
    status_now = accounting_status(client_id, session, auth)
    if not status_now.connected or status_now.master_data_from != "XERO":
        raise HTTPException(status.HTTP_409_CONFLICT, "connect and sync Xero first")
    enqueue(client_id)
    return status_now


@router.post("/clients/{client_id}/accounting/sync")
def sync_accounting(
    client_id: str,
    session: SessionDep,
    settings: SettingsDep,
    auth: XeroDep,
    system: XeroSystemDep,
) -> SyncOut:
    """Read accounts, tax rates and contacts from the connected Xero organisation. All or
    nothing; afterwards coding and checks use Xero's master data."""
    client = get_client(session, client_id)
    if client is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "client not found")
    try:
        report = sync_xero(session, settings, auth, client, system)
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


@router.post("/clients/{client_id}/accounting/disconnect")
def disconnect_accounting(
    client_id: str, session: SessionDep, settings: SettingsDep, auth: XeroDep
) -> AccountingStatus:
    if get_client(session, client_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "client not found")
    disconnect(session, settings, auth, client_id)
    session.commit()
    return accounting_status(client_id, session, auth)


def _counts(counts: Counts) -> SyncCounts:
    return SyncCounts(created=counts.created, updated=counts.updated, unchanged=counts.unchanged)


def _back(settings: Settings, **outcome: str) -> RedirectResponse:
    """Back to the Workbench's Clients page, saying what happened."""
    query = urlencode({key: value for key, value in outcome.items() if value})
    return RedirectResponse(f"{settings.workbench_url}/clients?{query}", status.HTTP_303_SEE_OTHER)
