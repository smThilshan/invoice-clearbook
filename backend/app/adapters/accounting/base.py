"""AccountingSystem port: what the core needs from any accounting system, in canonical types.

Grows only as adapters need it: master data (task 1.5), posting bills (tasks 5.1, 5.5), and
connecting by OAuth 2.0 (task 5.3). Every provider (Xero, later Zoho, ...) implements these in
its own folder and is listed once in `registry.py` (decision 43); nothing outside its folder
names it.
"""

from collections.abc import Callable, Mapping, MutableMapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
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


class AccountingSystemError(Exception):
    """The accounting system refused a request or could not be reached. The message says why,
    in words for the accountant; never a token or document contents."""


class AccountingSystem(Protocol):
    @property
    def provider(self) -> AccountingProvider: ...

    def fetch_master_data(self) -> MasterData: ...  # raises AccountingSystemError


# --- Posting (tasks 5.1, 5.5) ---------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PostingLine:
    description: str | None
    quantity: Decimal
    unit_amount: Decimal
    account_code: str
    tax_code: str  # the client's own tax rate code (master data), already checked


@dataclass(frozen=True, slots=True)
class Attachment:
    """The original file, attached to the posted document (decision 41)."""

    file_name: str
    mime_type: str
    content: bytes = field(repr=False)


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
    attachment: Attachment | None = None


@dataclass(frozen=True, slots=True)
class Posted:
    external_id: str
    total: Decimal | None  # as the accounting system computed it
    contact_external_id: str
    contact_created: bool
    due_date_from: str = "document"  # or the payment terms it was set from (decision 40)
    attached: bool = False  # the original file is on the posted document (decision 41)


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


class ConnectedSystem(AccountingSystem, PostingSystem, Protocol):
    """One connected organisation of a provider: master data in, documents out."""


# --- Connecting (task 5.3) -------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Tokens:
    access_token: str
    refresh_token: str
    expires_at: datetime
    scopes: str
    approval_id: str | None  # which sign-in approval these tokens came from, if the provider says


@dataclass(frozen=True, slots=True)
class Organisation:
    """One organisation the user allowed access to, in the provider's sign-in."""

    connection_id: str | None  # needed to disconnect, if the provider has one
    organisation_id: str  # sent with every API call
    name: str
    approval_id: str | None


class ConnectorError(Exception):
    """The provider refused the sign-in or could not be reached. Never carries a token."""


class Connector(Protocol):
    """OAuth 2.0 authorisation code flow against one provider."""

    def configured(self) -> bool: ...  # the app keys are in .env

    def authorize_url(self, state: str) -> str: ...

    def exchange(self, code: str) -> Tokens: ...

    def refresh(self, refresh_token: str) -> Tokens: ...

    def organisations(self, access_token: str) -> list[Organisation]: ...

    def disconnect(self, access_token: str, connection_id: str) -> None: ...
