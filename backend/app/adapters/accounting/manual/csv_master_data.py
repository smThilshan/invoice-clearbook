"""Master data from CSV files in our own simple template (see infra/samples/master_data/).

Exports from Xero or Zoho Books get a column mapping here once a real file has been seen.
Every row is checked and all problems are reported together; nothing is returned unless the
whole input is valid, so an import never half-applies.

Columns (header names are case-insensitive, extra columns are kept in `raw`):
  ledger_accounts.csv  code*, name*, class*, default_tax_code, active
  tax_rates.csv        code*, name*, rate, tax_type, active
  contacts.csv         name*, tax_number, is_supplier, active
"""

import csv
import io
from collections.abc import Callable, Iterator
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from app.adapters.accounting.base import (
    ContactRecord,
    LedgerAccountRecord,
    MasterData,
    MasterDataError,
    TaxRateRecord,
)
from app.domain.enums import AccountClass, AccountingProvider, TaxType

ACCOUNTS_FILE = "ledger_accounts.csv"
TAX_RATES_FILE = "tax_rates.csv"
CONTACTS_FILE = "contacts.csv"

_TRUE = frozenset({"yes", "y", "true", "1"})
_FALSE = frozenset({"no", "n", "false", "0"})


class _Keyed(Protocol):
    @property
    def external_id(self) -> str: ...


class ManualAccountingSystem:
    """CSV contents in, canonical master data out. `None` = that file was not supplied."""

    def __init__(
        self,
        *,
        accounts_csv: str | None = None,
        tax_rates_csv: str | None = None,
        contacts_csv: str | None = None,
    ) -> None:
        self._accounts_csv = accounts_csv
        self._tax_rates_csv = tax_rates_csv
        self._contacts_csv = contacts_csv

    @classmethod
    def from_directory(cls, directory: Path) -> "ManualAccountingSystem":
        """Read whichever of the three standard files exist in `directory`."""

        def read(name: str) -> str | None:
            path = directory / name
            return path.read_text(encoding="utf-8-sig") if path.is_file() else None

        return cls(
            accounts_csv=read(ACCOUNTS_FILE),
            tax_rates_csv=read(TAX_RATES_FILE),
            contacts_csv=read(CONTACTS_FILE),
        )

    @property
    def provider(self) -> AccountingProvider:
        return AccountingProvider.MANUAL

    def fetch_master_data(self) -> MasterData:
        problems: list[str] = []
        accounts = _parse(ACCOUNTS_FILE, self._accounts_csv, _account, problems)
        tax_rates = _parse(TAX_RATES_FILE, self._tax_rates_csv, _tax_rate, problems)
        contacts = _parse(CONTACTS_FILE, self._contacts_csv, _contact, problems)
        if problems:
            raise MasterDataError(problems)
        return MasterData(accounts=accounts, tax_rates=tax_rates, contacts=contacts)


class _Row:
    """One CSV row with typed getters that record problems instead of raising."""

    def __init__(self, file: str, number: int, values: dict[str, str], problems: list[str]):
        self.file, self.number, self.values, self._problems = file, number, values, problems

    def problem(self, message: str) -> None:
        self._problems.append(f"{self.file} row {self.number}: {message}")

    def text(self, column: str) -> str | None:
        return self.values.get(column) or None

    def required(self, column: str) -> str:
        value = self.text(column)
        if value is None:
            self.problem(f"'{column}' is required")
        return value or ""

    def flag(self, column: str, *, default: bool) -> bool:
        value = (self.text(column) or "").lower()
        if not value:
            return default
        if value in _TRUE | _FALSE:
            return value in _TRUE
        self.problem(f"'{column}' must be yes or no, got {self.values[column]!r}")
        return default

    def decimal(self, column: str, *, low: Decimal, high: Decimal) -> Decimal | None:
        value = self.text(column)
        if value is None:
            return None
        try:
            number = Decimal(value.rstrip("%").strip())
        except InvalidOperation:
            self.problem(f"'{column}' must be a number, got {value!r}")
            return None
        if not low <= number <= high:
            self.problem(f"'{column}' must be between {low} and {high}, got {value!r}")
        return number

    def choice[E: StrEnum](self, column: str, enum: type[E], *, required: bool) -> E | None:
        value = self.required(column) if required else self.text(column)
        if not value:
            return None
        try:
            return enum(value.upper().replace(" ", "_"))
        except ValueError:
            allowed = ", ".join(member.value for member in enum)
            self.problem(f"'{column}' must be one of {allowed}, got {value!r}")
            return None


REQUIRED_COLUMNS = {
    ACCOUNTS_FILE: {"code", "name", "class"},
    TAX_RATES_FILE: {"code", "name"},
    CONTACTS_FILE: {"name"},
}


def _parse[R: _Keyed](
    file: str,
    text: str | None,
    build: Callable[[_Row], R | None],
    problems: list[str],
) -> list[R] | None:
    if text is None:
        return None
    records: list[R] = []
    seen: dict[str, int] = {}
    for row in _rows(file, text, problems):
        record = build(row)
        if record is None:
            continue
        key = record.external_id
        if key in seen:
            row.problem(f"duplicate of row {seen[key]} ({key!r})")
            continue
        seen[key] = row.number
        records.append(record)
    return records


def _rows(file: str, text: str, problems: list[str]) -> Iterator[_Row]:
    reader = csv.reader(io.StringIO(text.lstrip("﻿")))
    header = next(reader, None)
    if not header:
        problems.append(f"{file}: file is empty")
        return
    columns = [name.strip().lower() for name in header]
    missing = REQUIRED_COLUMNS[file] - set(columns)
    if missing:
        problems.append(f"{file}: missing column(s) {', '.join(sorted(missing))}")
        return
    for number, cells in enumerate(reader, start=2):
        if not any(cell.strip() for cell in cells):
            continue  # blank line
        if len(cells) > len(columns):
            problems.append(f"{file} row {number}: more values than columns")
            continue
        values = dict(zip(columns, (cell.strip() for cell in cells), strict=False))
        yield _Row(file, number, values, problems)


def _account(row: _Row) -> LedgerAccountRecord | None:
    code, name = row.required("code"), row.required("name")
    account_class = row.choice("class", AccountClass, required=True)
    is_active = row.flag("active", default=True)
    if not code or not name or account_class is None:
        return None
    return LedgerAccountRecord(
        external_id=code,  # the manual adapter identifies accounts by code
        code=code,
        name=name,
        account_class=account_class,
        default_tax_code=row.text("default_tax_code"),
        is_active=is_active,
        raw=row.values,
    )


def _tax_rate(row: _Row) -> TaxRateRecord | None:
    code, name = row.required("code"), row.required("name")
    rate = row.decimal("rate", low=Decimal(0), high=Decimal(100))
    tax_type = row.choice("tax_type", TaxType, required=False)
    is_active = row.flag("active", default=True)
    if not code or not name:
        return None
    return TaxRateRecord(
        external_id=code,
        code=code,
        name=name,
        rate=rate,
        tax_type=tax_type,
        is_active=is_active,
        raw=row.values,
    )


def _contact(row: _Row) -> ContactRecord | None:
    name = row.required("name")
    is_supplier = row.flag("is_supplier", default=True)  # this template is purchase-side
    is_active = row.flag("active", default=True)
    if not name:
        return None
    return ContactRecord(
        # No IDs in a hand-made CSV: the normalised name is the identity.
        external_id=" ".join(name.casefold().split())[:64],
        name=name,
        tax_number=row.text("tax_number"),
        is_supplier=is_supplier,
        is_active=is_active,
        raw=row.values,
    )
