"""Xero master data (task 5.4): accounts, tax rates and contacts from a connected organisation,
as the canonical records the import stores (decision 20). Read only.

Only what the checks and coding use is kept. Xero contacts can carry bank details: they are
never copied (no `raw` beyond the fields below), so they cannot reach our database or logs.

Tax rates are mapped to the canonical VAT types from what Xero says about them: only rates that
can apply to expenses; Xero's fixed NONE code as no VAT; reverse charge, exempt and zero-rated by
name; the 5% rate as standard. Anything else stays unmapped (coding cannot use it) and is shown.
"""

from collections.abc import Callable, MutableMapping
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from app.adapters.accounting.base import (
    ContactRecord,
    LedgerAccountRecord,
    MasterData,
    Posted,
    PostingBill,
    TaxRateRecord,
)
from app.adapters.accounting.xero.bills import post_bill
from app.adapters.accounting.xero.client import XeroClient
from app.domain.enums import AccountClass, AccountingProvider, TaxType

CONTACTS_PAGE = 100  # Xero's page size for contacts
MAX_CONTACT_PAGES = 200  # 20,000 contacts: far beyond any client; stops a runaway loop
FIVE = Decimal("5")


class XeroAccountingSystem:
    """The AccountingSystem and PostingSystem ports for one connected Xero organisation."""

    def __init__(self, access_token: str, tenant_id: str, http: httpx.Client | None = None) -> None:
        self._xero = XeroClient(access_token, tenant_id, http)

    @property
    def provider(self) -> AccountingProvider:
        return AccountingProvider.XERO

    def fetch_master_data(self) -> MasterData:
        return MasterData(
            accounts=[_account(a) for a in self._xero.get("Accounts").get("Accounts", [])],
            tax_rates=[_tax_rate(t) for t in self._xero.get("TaxRates").get("TaxRates", [])],
            contacts=[_contact(c) for c in self._contacts()],
        )

    def post_bill(
        self,
        bill: PostingBill,
        *,
        key: str,
        progress: MutableMapping[str, Any],
        save: Callable[[str], None],
    ) -> Posted:
        return post_bill(self._xero, bill, key=key, progress=progress, save=save)

    def _contacts(self) -> list[dict[str, Any]]:
        found: list[dict[str, Any]] = []
        for page in range(1, MAX_CONTACT_PAGES + 1):
            batch = self._xero.get("Contacts", page=page, includeArchived="true")
            found += batch.get("Contacts", [])
            if len(batch.get("Contacts", [])) < CONTACTS_PAGE:
                break
        return found


def _account(a: dict[str, Any]) -> LedgerAccountRecord:
    return LedgerAccountRecord(
        external_id=a["AccountID"],
        code=a.get("Code") or None,
        name=a.get("Name") or "",
        account_class=AccountClass(a["Class"]),
        default_tax_code=a.get("TaxType") or None,
        is_active=a.get("Status") == "ACTIVE",
        raw={"Type": a.get("Type")},
    )


def _tax_rate(t: dict[str, Any]) -> TaxRateRecord:
    rate = _decimal(t.get("EffectiveRate", t.get("DisplayTaxRate")))
    return TaxRateRecord(
        external_id=t["TaxType"],  # Xero's key for a tax rate
        code=t["TaxType"],
        name=t.get("Name") or t["TaxType"],
        rate=rate,
        tax_type=map_tax_rate(t["TaxType"], t.get("Name") or "", rate, t.get("CanApplyToExpenses")),
        is_active=t.get("Status") == "ACTIVE",
        raw={"CanApplyToExpenses": t.get("CanApplyToExpenses")},
    )


def map_tax_rate(code: str, name: str, rate: Decimal | None, expenses: object) -> TaxType | None:
    """Which canonical VAT type a Xero tax rate stands for, for purchases; None = unmapped."""
    if expenses is False:
        return None  # sales-only rate: never on a purchase line
    if code == "NONE":
        return TaxType.NONE  # Xero's fixed code for "no tax", whatever the org named it
    label = f"{code} {name}".lower()
    if "reverse" in label:
        return TaxType.REVERSE_CHARGE
    if "exempt" in label:
        return TaxType.EXEMPT
    if "zero" in label:
        return TaxType.ZERO
    if "no vat" in label or "no tax" in label:
        return TaxType.NONE
    if rate == FIVE:
        return TaxType.STANDARD_5
    return None


def _contact(c: dict[str, Any]) -> ContactRecord:
    return ContactRecord(
        external_id=c["ContactID"],
        name=c.get("Name") or "",
        tax_number=c.get("TaxNumber") or None,
        is_supplier=bool(c.get("IsSupplier")),
        is_active=c.get("ContactStatus", "ACTIVE") == "ACTIVE",
    )


def _decimal(value: object) -> Decimal | None:
    try:
        return Decimal(str(value)) if value is not None else None
    except InvalidOperation:
        return None
