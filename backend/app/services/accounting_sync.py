"""Sync a connected client's master data from its accounting system (task 5.4).

One transaction: Xero's accounts, tax rates and contacts are imported (decision 20), rows that
are gone from Xero are marked inactive (never deleted: past coding still names them), and the
client switches to Xero's master data for coding and checks. Any failure changes nothing.
"""

from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.adapters.accounting.base import AccountingSystem, MasterData
from app.adapters.accounting.xero.auth import XeroAuth
from app.adapters.accounting.xero.client import XeroApiError
from app.adapters.accounting.xero.master_data import XeroAccountingSystem
from app.core.config import Settings
from app.domain.enums import AccountingProvider, TaxType
from app.models import Client, Contact, LedgerAccount, TaxRate
from app.models.master_data import MasterDataRow
from app.services.accounting_connections import (
    AccountingConnectionError,
    access_token,
    connection_of,
)
from app.services.master_data import Counts, import_master_data

SystemFactory = Callable[[str, str], AccountingSystem]  # (access token, tenant id) -> system


@dataclass(frozen=True, slots=True)
class TaxMapping:
    code: str
    name: str
    tax_type: TaxType | None  # None = not used for purchases here


@dataclass(frozen=True, slots=True)
class SyncReport:
    accounts: Counts
    tax_rates: Counts
    contacts: Counts
    deactivated: int  # rows no longer in Xero
    tax_mapping: list[TaxMapping]  # active rates, as matched to our VAT types


def sync_xero(
    session: Session,
    settings: Settings,
    auth: XeroAuth,
    client: Client,
    system: SystemFactory = XeroAccountingSystem,
) -> SyncReport:
    """The caller commits; on error it rolls back and nothing has changed."""
    connection = connection_of(session, client.client_id)
    if connection is None:
        raise AccountingConnectionError("this client is not connected to Xero")
    token = access_token(session, settings, auth, client.client_id)
    source = system(token, connection.tenant_id)
    try:
        data = source.fetch_master_data()
    except XeroApiError as exc:
        raise AccountingConnectionError(str(exc)) from exc

    provider = AccountingProvider.XERO
    report = import_master_data(session, client_id=client.client_id, source=_Fetched(data))
    deactivated = sum(
        _deactivate_missing(session, model, client.client_id, {r.external_id for r in records})
        for model, records in (
            (LedgerAccount, data.accounts or []),
            (TaxRate, data.tax_rates or []),
            (Contact, data.contacts or []),
        )
    )
    client.accounting_provider = provider
    session.flush()
    return SyncReport(
        accounts=report.accounts or Counts(),
        tax_rates=report.tax_rates or Counts(),
        contacts=report.contacts or Counts(),
        deactivated=deactivated,
        tax_mapping=[
            TaxMapping(r.code, r.name, r.tax_type) for r in data.tax_rates or [] if r.is_active
        ],
    )


class _Fetched:
    """Already-fetched data as an AccountingSystem, so the import does not call Xero again."""

    provider = AccountingProvider.XERO

    def __init__(self, data: MasterData) -> None:
        self._data = data

    def fetch_master_data(self) -> MasterData:
        return self._data


def _deactivate_missing(
    session: Session, model: type[MasterDataRow], client_id: str, present: set[str]
) -> int:
    result = session.execute(
        update(model)
        .where(
            model.client_id == client_id,
            model.provider == AccountingProvider.XERO,
            model.is_active.is_(True),
            model.external_id.not_in(present),
        )
        .values(is_active=False)
        .execution_options(synchronize_session="fetch")
    )
    return int(result.rowcount or 0)  # type: ignore[attr-defined]
