"""Sync a connected client's master data from its accounting system (task 5.4, decision 43).

One transaction: the provider's accounts, tax rates and contacts are imported (decision 20),
rows that are gone from it are marked inactive (never deleted: past coding still names them),
and the client switches to that master data for coding and checks. Any failure changes nothing.
"""

from dataclasses import dataclass

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.adapters.accounting.base import AccountingSystemError, MasterData
from app.adapters.accounting.registry import Registry
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
    deactivated: int  # rows no longer in the accounting system
    tax_mapping: list[TaxMapping]  # active rates, as matched to our VAT types


def sync(session: Session, settings: Settings, registry: Registry, client: Client) -> SyncReport:
    """The caller commits; on error it rolls back and nothing has changed."""
    connection = connection_of(session, client.client_id)
    if connection is None:
        raise AccountingConnectionError("this client is not connected to an accounting system")
    provider = connection.provider
    token = access_token(session, settings, registry, client.client_id)
    try:
        data = registry[provider].system(token, connection.tenant_id).fetch_master_data()
    except AccountingSystemError as exc:
        raise AccountingConnectionError(str(exc)) from exc

    source = _Fetched(provider, data)
    report = import_master_data(session, client_id=client.client_id, source=source)
    deactivated = sum(
        _deactivate_missing(
            session, model, client.client_id, provider, {r.external_id for r in records}
        )
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
    """Already-fetched data as an AccountingSystem, so the import does not call it again."""

    def __init__(self, provider: AccountingProvider, data: MasterData) -> None:
        self._provider = provider
        self._data = data

    @property
    def provider(self) -> AccountingProvider:
        return self._provider

    def fetch_master_data(self) -> MasterData:
        return self._data


def _deactivate_missing(
    session: Session,
    model: type[MasterDataRow],
    client_id: str,
    provider: AccountingProvider,
    present: set[str],
) -> int:
    result = session.execute(
        update(model)
        .where(
            model.client_id == client_id,
            model.provider == provider,
            model.is_active.is_(True),
            model.external_id.not_in(present),
        )
        .values(is_active=False)
        .execution_options(synchronize_session="fetch")
    )
    return int(result.rowcount or 0)  # type: ignore[attr-defined]
