"""Clients (restaurants): their folders for the Run screen's pickers (decision 25), their
master data (CSV import, task 4.2) and the code options for the review form.

Clients are added with `make add-client` until the admin screen exists (behind login, Phase 6).
No login yet: the API listens on localhost only.
"""

import uuid
from datetime import date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.adapters.accounting.base import MasterDataError
from app.adapters.accounting.manual import ManualAccountingSystem
from app.core.config import Settings, get_settings
from app.core.db import get_session
from app.domain.enums import AccountClass, AccountingProvider, TaxType
from app.models import Client
from app.pipeline import coding
from app.pipeline.folders import InboxError, folder_date_from_path
from app.services.clients import browse, get_client, list_clients
from app.services.master_data import Counts, import_master_data
from app.services.runs import recent_runs

MAX_CSV_BYTES = 5 * 1024 * 1024  # per file; a chart of accounts or supplier list is far smaller

router = APIRouter(prefix="/clients", tags=["clients"])


class ClientOut(BaseModel):
    client_id: str
    name: str


class Folder(BaseModel):
    name: str
    path: str  # relative to the client's folder; send it back as the run's `folder`
    date: str | None  # the date this folder stands for, if its name holds one


class FolderListing(BaseModel):
    path: str
    folders: list[Folder]


class ImportCounts(BaseModel):
    created: int
    updated: int
    unchanged: int


class MasterDataImportOut(BaseModel):
    """Per table; None = that file was not uploaded."""

    accounts: ImportCounts | None
    tax_rates: ImportCounts | None
    contacts: ImportCounts | None


class RunOption(BaseModel):
    run_id: uuid.UUID
    folder: str  # relative to the client's folder; "" = all of it
    folder_date: date | None
    created_at: datetime
    documents: int


class AccountOption(BaseModel):
    code: str
    name: str
    account_class: AccountClass


class TaxOption(BaseModel):
    tax_type: TaxType
    code: str  # the client's tax rate code for it


class CodingOptions(BaseModel):
    """What the review form may choose from: built from the client's active master data
    (rule 14), purchase accounts only."""

    accounts: list[AccountOption]
    tax_types: list[TaxOption]


@router.get("")
def get_clients(session: Annotated[Session, Depends(get_session)]) -> list[ClientOut]:
    return [ClientOut(client_id=c.client_id, name=c.name) for c in list_clients(session)]


@router.get("/{client_id}/runs")
def get_runs(
    client_id: str,
    session: Annotated[Session, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> list[RunOption]:
    """Latest runs that took in documents, newest first: the queue's run filter."""
    _found(session, client_id)
    return [
        RunOption(
            run_id=r.run.id,
            folder=r.run.folder,
            folder_date=r.run.folder_date,
            created_at=r.run.created_at,
            documents=r.documents,
        )
        for r in recent_runs(session, client_id, limit=limit)
    ]


@router.get("/{client_id}/folders")
def get_folders(
    client_id: str,
    session: Annotated[Session, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    path: Annotated[str, Query(description="Folder inside the client's folder")] = "",
) -> FolderListing:
    client = _found(session, client_id)
    try:
        names = browse(client, path)
    except InboxError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    base = path.strip("/")
    today = settings.business_today()
    folders = []
    for name in names:
        relative = f"{base}/{name}" if base else name
        found = folder_date_from_path(relative, today=today)
        folders.append(Folder(name=name, path=relative, date=found.isoformat() if found else None))
    return FolderListing(path=base, folders=folders)


@router.post("/{client_id}/master-data")
def upload_master_data(
    client_id: str,
    session: Annotated[Session, Depends(get_session)],
    ledger_accounts: Annotated[UploadFile | None, File()] = None,
    tax_rates: Annotated[UploadFile | None, File()] = None,
    contacts: Annotated[UploadFile | None, File()] = None,
) -> MasterDataImportOut:
    """Import any of the three CSVs (template: infra/samples/master_data/). All or nothing:
    422 lists every problem and changes nothing. Re-uploading is safe."""
    client = _found(session, client_id)
    if client.accounting_provider is not AccountingProvider.MANUAL:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"master data for this client comes from {client.accounting_provider.value}",
        )
    if ledger_accounts is None and tax_rates is None and contacts is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "no file uploaded")
    source = ManualAccountingSystem(
        accounts_csv=_csv_text(ledger_accounts),
        tax_rates_csv=_csv_text(tax_rates),
        contacts_csv=_csv_text(contacts),
    )
    try:
        with session.begin_nested():  # all or nothing
            report = import_master_data(session, client_id=client.client_id, source=source)
    except MasterDataError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, {"problems": exc.problems}
        ) from exc
    session.commit()
    return MasterDataImportOut(
        accounts=_counts(report.accounts),
        tax_rates=_counts(report.tax_rates),
        contacts=_counts(report.contacts),
    )


@router.get("/{client_id}/coding-options")
def get_coding_options(
    client_id: str, session: Annotated[Session, Depends(get_session)]
) -> CodingOptions:
    client = _found(session, client_id)
    chart = coding.load_chart(
        session, client_id=client.client_id, provider=client.accounting_provider
    )
    return CodingOptions(
        accounts=[
            AccountOption(code=a.code, name=a.name, account_class=a.account_class)
            for a in chart.purchase_accounts()
        ],
        tax_types=[TaxOption(tax_type=t, code=c) for t, c in chart.tax_codes.items()],
    )


def _found(session: Session, client_id: str) -> Client:
    client = get_client(session, client_id)
    if client is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "client not found")
    return client


def _csv_text(upload: UploadFile | None) -> str | None:
    if upload is None:
        return None
    data = upload.file.read(MAX_CSV_BYTES + 1)
    name = upload.filename or "file"
    if len(data) > MAX_CSV_BYTES:
        raise HTTPException(
            status.HTTP_413_CONTENT_TOO_LARGE, f"{name}: larger than {MAX_CSV_BYTES} bytes"
        )
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, f"{name}: not a UTF-8 CSV file"
        ) from exc


def _counts(counts: Counts | None) -> ImportCounts | None:
    if counts is None:
        return None
    return ImportCounts(created=counts.created, updated=counts.updated, unchanged=counts.unchanged)
