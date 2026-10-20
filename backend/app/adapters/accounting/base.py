"""AccountingSystem port: what the core needs from any accounting system, in canonical types.

Grows only as adapters need it: master data now (task 1.5), posting in task 5.1.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Protocol

from app.domain.enums import AccountClass, AccountingProvider, TaxType


@dataclass(frozen=True, slots=True)
class LedgerAccountRecord:
    external_id: str
    code: str | None
    name: str
    account_class: AccountClass
    default_tax_code: str | None = None
    is_active: bool = True
    raw: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class TaxRateRecord:
    external_id: str
    code: str
    name: str
    rate: Decimal | None = None
    tax_type: TaxType | None = None  # None = not mapped; coding cannot use it
    is_active: bool = True
    raw: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ContactRecord:
    external_id: str
    name: str
    tax_number: str | None = None
    is_supplier: bool = False
    is_active: bool = True
    raw: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class MasterData:
    """None = not supplied, leave existing rows alone. An empty list = supplied with no rows."""

    accounts: Sequence[LedgerAccountRecord] | None = None
    tax_rates: Sequence[TaxRateRecord] | None = None
    contacts: Sequence[ContactRecord] | None = None


class MasterDataError(Exception):
    """Master data was rejected. `problems` lists every issue found, not just the first."""

    def __init__(self, problems: Sequence[str]) -> None:
        super().__init__(f"{len(problems)} problem(s) in master data")
        self.problems = list(problems)


class AccountingSystem(Protocol):
    @property
    def provider(self) -> AccountingProvider: ...

    def fetch_master_data(self) -> MasterData: ...
