"""AccountingSystem port: what the core needs from any accounting system, in canonical types.

Grows only as adapters need it: master data (task 1.5), posting bills (tasks 5.1, 5.5).
"""

from collections.abc import Callable, Mapping, MutableMapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Protocol

from app.domain.enums import AccountClass, AccountingProvider, AmountType, TaxType


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


# --- Posting (tasks 5.1, 5.5) ---------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PostingLine:
    description: str | None
    quantity: Decimal
    unit_amount: Decimal
    account_code: str
    tax_code: str  # the client's own tax rate code (master data), already checked


@dataclass(frozen=True, slots=True)
class PostingBill:
    """An approved supplier bill, checked against the client's master data."""

    supplier_name: str
    supplier_tax_number: str | None
    supplier_external_id: str | None  # the matched contact in the accounting system, if any
    invoice_number: str | None
    invoice_date: date | None
    due_date: date | None
    currency: str | None
    amount_type: AmountType
    lines: Sequence[PostingLine]
    note: str  # history note: who created and approved it


@dataclass(frozen=True, slots=True)
class Posted:
    external_id: str
    total: Decimal | None  # as the accounting system computed it
    contact_external_id: str
    contact_created: bool


class PostingRefused(Exception):  # noqa: N818 - a business outcome, not a program error
    """The accounting system will not take this document as it is: back to review, with the
    reason. Never retried as it is."""


class PostingUnavailable(Exception):  # noqa: N818
    """Temporary (rate limit, timeout, server down): try again later."""

    def __init__(self, message: str, retry_after: int | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class PostingSystem(Protocol):
    """Posts approved documents. `progress` holds each finished sub-step's result; `save` stores
    it at once, so a crash resumes from the failed sub-step (architecture §12). `key` is sent as
    the idempotency key: a repeat never creates a second document."""

    def post_bill(
        self,
        bill: PostingBill,
        *,
        key: str,
        progress: MutableMapping[str, Any],
        save: Callable[[str], None],
    ) -> Posted: ...
