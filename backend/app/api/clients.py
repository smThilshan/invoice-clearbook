"""Clients (any business, decision 35): their folders for the Run screen's pickers, their
master data (CSV import, task 4.2) and the code options for the review form.

Clients are added on the Run screen (decision 35) or with `make add-client`. Every route needs
a login (decision 71); importing master data and changing how a client's lines and expenses
are sent (decisions 73, 89) need an admin, and so do removing, restoring and deleting a client
(decision 91).
"""

import logging
import uuid
from datetime import date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.adapters.accounting.base import MasterDataError
from app.adapters.accounting.manual import ManualAccountingSystem
from app.adapters.storage import Storage, get_storage
from app.adapters.storage.keys import client_folder
from app.api.accounting import RegistryDep
from app.api.auth import AdminDep, require_admin
from app.core.config import Settings, get_settings
from app.core.db import get_session
from app.domain.enums import AccountClass, AccountingProvider, ExpensesSentAs, LineGrouping, TaxType
from app.models import Client
from app.pipeline import coding
from app.pipeline.folders import InboxError, folder_date_from_path
from app.services.clients import (
    ClientError,
    add_client,
    browse,
    get_client,
    list_clients,
    list_removed_clients,
    new_client_id,
    remove_client,
    restore_client,
    set_expense_sending,
)
from app.services.company_deletion import delete_company, preview, removed_client
from app.services.master_data import Counts, import_master_data
from app.services.runs import recent_runs

MAX_CSV_BYTES = 5 * 1024 * 1024  # per file; a chart of accounts or supplier list is far smaller

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/clients", tags=["clients"])


class ClientOut(BaseModel):
    client_id: str
    name: str
    line_grouping: LineGrouping  # how approved lines are sent (decision 73)
    expenses_sent_as: ExpensesSentAs  # decision 89
    paid_through: str | None  # the account expenses are usually paid through; None = each time


class DeletionPreviewOut(BaseModel):
    """What deleting a removed company would remove (decision 91)."""

    documents: int
    posted: int  # in the accounting system: they stay there
    connected_to: str | None  # disconnected first
    blocked: str | None  # why it cannot be deleted now; None = it can


class DeleteCompany(BaseModel):
    name: str  # the company's name, typed by the admin to confirm


class CompanyDeleted(BaseModel):
    documents: int
    files_removed: bool  # False: a stray folder is left in storage (logged), nothing else


def get_company_storage(settings: Annotated[Settings, Depends(get_settings)]) -> Storage:
    return get_storage(settings)


class ClientSettings(BaseModel):
    """Only the settings sent are changed."""

    line_grouping: LineGrouping | None = None
    expenses_sent_as: ExpensesSentAs | None = None
    paid_through: str | None = None  # send null to choose on each expense


class ClientCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)


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
    code: str  # the value a line stores
    name: str
    account_class: AccountClass
    label: str  # what the list shows: "310 Cost of Goods Sold", or the name without a number


class TaxOption(BaseModel):
    tax_type: TaxType
    code: str  # the client's tax rate code for it


class PaymentAccountOption(BaseModel):
    code: str  # the value stored
    label: str
    group: str  # as the accounting system's own list groups it: "Cash", "Bank", "Equity"


class CodingOptions(BaseModel):
    """What the review form may choose from: built from the client's active master data
    (rule 14), purchase accounts only; and the accounts an expense may be paid through
    (decision 89)."""

    accounts: list[AccountOption]
    tax_types: list[TaxOption]
    paid_through: list[PaymentAccountOption]


@router.get("")
def get_clients(session: Annotated[Session, Depends(get_session)]) -> list[ClientOut]:
    return [_out(c) for c in list_clients(session)]


@router.get("/removed", dependencies=[Depends(require_admin)])
def get_removed_clients(session: Annotated[Session, Depends(get_session)]) -> list[ClientOut]:
    """Removed clients, to restore (decision 91)."""
    return [_out(c) for c in list_removed_clients(session)]


@router.post("/{client_id}/remove")
def remove(
    client_id: str, session: Annotated[Session, Depends(get_session)], admin: AdminDep
) -> ClientOut:
    """Hide the client everywhere; nothing is deleted. 409 while it still has work in flight."""
    client = _found(session, client_id)
    try:
        remove_client(session, client)
    except ClientError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    session.commit()
    logger.info("client %s removed by user %s", client.client_id, admin.id)
    return _out(client)


@router.post("/{client_id}/restore")
def restore(
    client_id: str, session: Annotated[Session, Depends(get_session)], admin: AdminDep
) -> ClientOut:
    client = restore_client(session, client_id)
    if client is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no removed company with that id")
    session.commit()
    logger.info("client %s restored by user %s", client.client_id, admin.id)
    return _out(client)


@router.get("/{client_id}/deletion", dependencies=[Depends(require_admin)])
def get_deletion_preview(
    client_id: str, session: Annotated[Session, Depends(get_session)], registry: RegistryDep
) -> DeletionPreviewOut:
    """Only a removed company can be deleted: 404 for any other."""
    client = _removed(session, client_id)
    return DeletionPreviewOut(**vars(preview(session, registry, client)))


@router.post("/{client_id}/delete")
def delete(
    client_id: str,
    body: DeleteCompany,
    session: Annotated[Session, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    registry: RegistryDep,
    storage: Annotated[Storage, Depends(get_company_storage)],
    admin: AdminDep,
) -> CompanyDeleted:
    """Delete a removed company permanently, files included. Cannot be undone. 409 with the
    reason when it cannot be deleted now or the name typed does not match."""
    client = _removed(session, client_id, for_update=True)
    try:
        documents = delete_company(
            session, settings, registry, client, typed_name=body.name, actor=admin.email
        )
    except ClientError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    session.commit()
    logger.info("client %s deleted by user %s: %d documents", client_id, admin.id, documents)
    try:  # after the commit: at worst a stray folder, never a record pointing to a lost file
        storage.delete_folder(client_folder(client_id))
    except OSError:
        logger.warning("client %s: its stored files could not all be removed", client_id)
        return CompanyDeleted(documents=documents, files_removed=False)
    return CompanyDeleted(documents=documents, files_removed=True)


@router.patch("/{client_id}", dependencies=[Depends(require_admin)])
def update_client(
    client_id: str,
    body: ClientSettings,
    session: Annotated[Session, Depends(get_session)],
    registry: RegistryDep,
) -> ClientOut:
    """Documents approved from now on are sent this way; ones already approved keep theirs."""
    client = _found(session, client_id)
    sent = body.model_fields_set
    if sent & {"expenses_sent_as", "paid_through"}:
        try:  # checked before anything changes
            set_expense_sending(
                session,
                registry,
                client,
                sent_as=body.expenses_sent_as or client.expenses_sent_as,
                paid_through=body.paid_through if "paid_through" in sent else client.paid_through,
            )
        except ClientError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    if body.line_grouping is not None:
        client.line_grouping = body.line_grouping
    session.commit()
    return _out(client)


@router.post("", status_code=status.HTTP_201_CREATED)
def create_client(
    body: ClientCreate, session: Annotated[Session, Depends(get_session)]
) -> ClientOut:
    """A new client from the Run screen: just a name; its id is made from the name. Its
    folder is picked per run (decision 35)."""
    try:
        client = add_client(session, client_id=new_client_id(session, body.name), name=body.name)
        session.commit()
    except ClientError as exc:
        session.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    return _out(client)


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


@router.post("/{client_id}/master-data", dependencies=[Depends(require_admin)])
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
            f"master data for this company comes from {client.accounting_provider.value}",
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
            AccountOption(code=a.code, name=a.name, account_class=a.account_class, label=a.label)
            for a in chart.purchase_accounts()
        ],
        tax_types=[TaxOption(tax_type=t, code=c) for t, c in chart.tax_codes.items()],
        paid_through=[
            PaymentAccountOption(code=a.code, label=a.label, group=a.pays)
            for a in sorted(chart.payment_accounts(), key=lambda a: (a.pays or "", a.label))
            if a.pays
        ],
    )


def _out(client: Client) -> ClientOut:
    return ClientOut(
        client_id=client.client_id,
        name=client.name,
        line_grouping=client.line_grouping,
        expenses_sent_as=client.expenses_sent_as,
        paid_through=client.paid_through,
    )


def _found(session: Session, client_id: str) -> Client:
    client = get_client(session, client_id)
    if client is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "company not found")
    return client


def _removed(session: Session, client_id: str, *, for_update: bool = False) -> Client:
    client = removed_client(session, client_id, for_update=for_update)
    if client is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no removed company with that id")
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
