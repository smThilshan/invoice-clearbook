"""Import master data (accounts, tax rates, contacts) from any accounting adapter.

Runs inside the caller's transaction: either everything is applied or nothing is. Rows are
matched by (client, provider, external_id) and updated in place, so re-running an import is
safe. Rows missing from the input are left alone: an import never deletes.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.accounting.base import (
    AccountingSystem,
    ContactRecord,
    LedgerAccountRecord,
    MasterData,
    MasterDataError,
    TaxRateRecord,
)
from app.domain.enums import AccountingProvider
from app.models import Contact, LedgerAccount, TaxRate
from app.models.master_data import MasterDataRow


@dataclass
class Counts:
    created: int = 0
    updated: int = 0
    unchanged: int = 0


@dataclass(frozen=True)
class ImportReport:
    """Per table; None = that table was not part of the import."""

    accounts: Counts | None
    tax_rates: Counts | None
    contacts: Counts | None


def import_master_data(
    session: Session, *, client_id: str, source: AccountingSystem
) -> ImportReport:
    data = source.fetch_master_data()  # raises MasterDataError on invalid input
    provider = source.provider
    _check_default_tax_codes(session, client_id, provider, data)

    report = ImportReport(
        accounts=_upsert(session, LedgerAccount, client_id, provider, data.accounts, _account),
        tax_rates=_upsert(session, TaxRate, client_id, provider, data.tax_rates, _tax_rate),
        contacts=_upsert(session, Contact, client_id, provider, data.contacts, _contact),
    )
    session.flush()
    return report


def _check_default_tax_codes(
    session: Session, client_id: str, provider: AccountingProvider, data: MasterData
) -> None:
    if not data.accounts:
        return
    known = {rate.code for rate in data.tax_rates or ()}
    known |= set(
        session.scalars(
            select(TaxRate.code).where(TaxRate.client_id == client_id, TaxRate.provider == provider)
        )
    )
    problems = [
        f"account {account.code}: default tax code {account.default_tax_code!r} is not a known "
        "tax rate"
        for account in data.accounts
        if account.default_tax_code and account.default_tax_code not in known
    ]
    if problems:
        raise MasterDataError(problems)


def _upsert[Row: MasterDataRow, Rec: (LedgerAccountRecord, TaxRateRecord, ContactRecord)](
    session: Session,
    model: type[Row],
    client_id: str,
    provider: AccountingProvider,
    records: Sequence[Rec] | None,
    values_of: Callable[[Rec], Mapping[str, Any]],
) -> Counts | None:
    if records is None:
        return None
    existing = {
        row.external_id: row
        for row in session.scalars(
            select(model).where(model.client_id == client_id, model.provider == provider)
        )
    }
    counts = Counts()
    for record in records:
        values = values_of(record)
        row = existing.get(record.external_id)
        if row is None:
            row = model()
            row.client_id, row.provider, row.external_id = client_id, provider, record.external_id
            _assign(row, values)
            session.add(row)
            counts.created += 1
        elif any(getattr(row, key) != value for key, value in values.items()):
            _assign(row, values)
            counts.updated += 1
        else:
            counts.unchanged += 1
    return counts


def _assign(row: MasterDataRow, values: Mapping[str, Any]) -> None:
    for key, value in values.items():
        setattr(row, key, value)


def _account(record: LedgerAccountRecord) -> Mapping[str, Any]:
    return {
        "code": record.code,
        "name": record.name,
        "account_class": record.account_class,
        "default_tax_code": record.default_tax_code,
        "is_active": record.is_active,
        "raw": dict(record.raw),
    }


def _tax_rate(record: TaxRateRecord) -> Mapping[str, Any]:
    return {
        "code": record.code,
        "name": record.name,
        "rate": record.rate,
        "tax_type": record.tax_type,
        "is_active": record.is_active,
        "raw": dict(record.raw),
    }


def _contact(record: ContactRecord) -> Mapping[str, Any]:
    return {
        "name": record.name,
        "tax_number": record.tax_number,
        "is_supplier": record.is_supplier,
        "is_active": record.is_active,
        "raw": dict(record.raw),
    }
