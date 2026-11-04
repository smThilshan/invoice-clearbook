"""Zoho Books master data in neutral records (decision 74): chart of accounts, taxes, vendors.

- Accounts: Zoho's account code is optional (none in a new organisation); an account without one
  is referenced by its Zoho ID, which is never shown (`CODE_IS_REFERENCE`).
- Taxes: one per rate, mapped by name and percentage. Zoho has no "no tax" rate: a line without
  VAT is sent without a tax, so a fixed `NO_VAT` code stands for it.
- Vendors: the list carries no TRN (only each vendor's own record does, one call each); the
  vendor is found by TRN when a bill is sent.
"""

from decimal import Decimal, InvalidOperation
from typing import Any

from app.adapters.accounting.base import (
    CODE_IS_REFERENCE,
    ContactRecord,
    LedgerAccountRecord,
    MasterData,
    TaxRateRecord,
)
from app.adapters.accounting.zoho.client import ZohoClient
from app.domain.enums import AccountClass, TaxType

NO_VAT = "NO_VAT"  # no tax on the line: Zoho is sent none
FIVE = Decimal(5)
# Zoho's account types (chart of accounts API) by class; any other type is an asset.
_CLASSES = {
    AccountClass.EXPENSE: {"expense", "cost_of_goods_sold", "other_expense"},
    AccountClass.REVENUE: {"income", "other_income"},
    AccountClass.EQUITY: {"equity"},
    AccountClass.LIABILITY: {
        "other_current_liability", "current_liability", "credit_card", "long_term_liability",
        "other_liability", "accounts_payable", "output_tax", "overseas_tax_payable",
        "non_current_liability", "loans_and_borrowing",
    },
}  # fmt: skip


def fetch_master_data(zoho: ZohoClient, organisation_id: str) -> MasterData:
    accounts = zoho.pages("chartofaccounts", "chartofaccounts", filter_by="AccountType.All")
    taxes = zoho.get("settings/taxes").get("taxes") or []
    vendors = zoho.pages("contacts", "contacts", contact_type="vendor")
    return MasterData(
        accounts=[_account(a) for a in accounts],
        tax_rates=[_tax_rate(t) for t in taxes] + [_no_vat()],
        contacts=[_contact(c) for c in vendors],
        organisation_ref=organisation_id,
    )


def account_class(account_type: str) -> AccountClass:
    found = (cls for cls, types in _CLASSES.items() if account_type in types)
    return next(found, AccountClass.ASSET)


def map_tax(name: str, rate: Decimal | None) -> TaxType | None:
    """Which canonical VAT type a Zoho tax stands for; None = unmapped."""
    label = name.lower()
    if "reverse" in label:
        return TaxType.REVERSE_CHARGE
    if "exempt" in label:
        return TaxType.EXEMPT
    if "zero" in label or rate == 0:
        return TaxType.ZERO
    if rate == FIVE:
        return TaxType.STANDARD_5
    return None


def _account(a: dict[str, Any]) -> LedgerAccountRecord:
    code = (a.get("account_code") or "").strip()
    return LedgerAccountRecord(
        external_id=str(a["account_id"]),
        code=code or str(a["account_id"]),
        name=a.get("account_name") or "",
        account_class=account_class(a.get("account_type") or ""),
        is_active=bool(a.get("is_active", True)),
        raw={"account_type": a.get("account_type"), CODE_IS_REFERENCE: not code},
    )


def _tax_rate(t: dict[str, Any]) -> TaxRateRecord:
    rate = _decimal(t.get("tax_percentage"))
    single = t.get("tax_type", "tax") == "tax"  # groups and compound taxes are not mapped
    return TaxRateRecord(
        external_id=str(t["tax_id"]),
        code=str(t["tax_id"]),
        name=t.get("tax_name") or str(t["tax_id"]),
        rate=rate,
        tax_type=map_tax(t.get("tax_name") or "", rate) if single else None,
        is_active=not t.get("is_inactive", False),
        raw={"tax_type": t.get("tax_type"), "tax_specific_type": t.get("tax_specific_type")},
    )


def _no_vat() -> TaxRateRecord:
    return TaxRateRecord(
        external_id=NO_VAT, code=NO_VAT, name="No VAT", rate=Decimal(0), tax_type=TaxType.NONE
    )


def _contact(c: dict[str, Any]) -> ContactRecord:
    return ContactRecord(
        external_id=str(c["contact_id"]),
        name=c.get("contact_name") or "",
        tax_number=c.get("tax_reg_no") or None,  # absent from the list; kept if Zoho adds it
        is_supplier=True,
        is_active=c.get("status", "active") == "active",
    )


def _decimal(value: object) -> Decimal | None:
    try:
        return Decimal(str(value)) if value is not None else None
    except InvalidOperation:
        return None
