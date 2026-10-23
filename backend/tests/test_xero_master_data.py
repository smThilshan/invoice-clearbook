"""Xero master data (task 5.4): Xero's answers -> canonical records, tax-rate matching, paging."""

from decimal import Decimal
from typing import Any

import httpx
import pytest

from app.adapters.accounting.xero.client import XeroApiError
from app.adapters.accounting.xero.master_data import (
    CONTACTS_PAGE,
    XeroAccountingSystem,
    map_tax_rate,
)
from app.domain.enums import AccountClass, TaxType

ACCOUNTS = [
    {"AccountID": "a1", "Code": "400", "Name": "Advertising", "Class": "EXPENSE",
     "Type": "EXPENSE", "TaxType": "INPUT", "Status": "ACTIVE"},
    {"AccountID": "a2", "Code": "999", "Name": "Old account", "Class": "EXPENSE",
     "Type": "EXPENSE", "TaxType": "", "Status": "ARCHIVED"},
]  # fmt: skip
TAX_RATES = [
    {"TaxType": "INPUT", "Name": "Standard Rated Expenses", "EffectiveRate": 5.0,
     "CanApplyToExpenses": True, "Status": "ACTIVE"},
    {"TaxType": "OUTPUT", "Name": "Standard Rated Income", "EffectiveRate": 5.0,
     "CanApplyToExpenses": False, "Status": "ACTIVE"},
]  # fmt: skip


def xero(contacts: list[dict[str, Any]], seen: list[httpx.Request] | None = None) -> httpx.Client:
    def answer(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        resource = request.url.path.rsplit("/", 1)[1]
        if resource == "Contacts":
            page = int(request.url.params["page"])
            batch = contacts[(page - 1) * CONTACTS_PAGE : page * CONTACTS_PAGE]
            return httpx.Response(200, json={"Contacts": batch})
        listing = {"Accounts": ACCOUNTS, "TaxRates": TAX_RATES}[resource]
        return httpx.Response(200, json={resource: listing})

    return httpx.Client(transport=httpx.MockTransport(answer))


def contact(n: int, **extra: Any) -> dict[str, Any]:
    return {"ContactID": f"c{n}", "Name": f"Supplier {n}", "IsSupplier": True,
            "ContactStatus": "ACTIVE", **extra}  # fmt: skip


def test_xero_answers_become_canonical_records_and_bank_details_are_not_kept() -> None:
    bank = {"BankAccountDetails": "made-up 0000", "TaxNumber": "100000000000003"}
    data = XeroAccountingSystem("token", "tenant", xero([contact(1, **bank)])).fetch_master_data()

    assert data.accounts and data.tax_rates and data.contacts
    advertising, old = data.accounts
    assert (advertising.code, advertising.account_class, advertising.default_tax_code) == (
        "400", AccountClass.EXPENSE, "INPUT")  # fmt: skip
    assert advertising.is_active and not old.is_active and old.default_tax_code is None
    standard, sales = data.tax_rates
    assert (standard.tax_type, standard.rate) == (TaxType.STANDARD_5, Decimal("5.0"))
    assert sales.tax_type is None  # sales-only: never on a purchase
    (supplier,) = data.contacts
    assert (supplier.tax_number, supplier.is_supplier) == ("100000000000003", True)
    assert "made-up" not in repr(supplier)


def test_every_request_names_the_organisation_and_contacts_are_read_page_by_page() -> None:
    seen: list[httpx.Request] = []
    many = [contact(n) for n in range(CONTACTS_PAGE + 1)]

    data = XeroAccountingSystem("token", "tenant-1", xero(many, seen)).fetch_master_data()

    assert data.contacts is not None and len(data.contacts) == CONTACTS_PAGE + 1
    assert [r.url.params.get("page") for r in seen] == [None, None, "1", "2"]
    assert all(r.headers["xero-tenant-id"] == "tenant-1" for r in seen)
    assert all(r.headers["authorization"] == "Bearer token" for r in seen)


@pytest.mark.parametrize(
    ("code", "name", "rate", "expected"),
    [
        ("INPUT", "Standard Rated Expenses", "5", TaxType.STANDARD_5),
        ("ZERORATEDINPUT", "Zero Rated Expenses", "0", TaxType.ZERO),
        ("EXEMPTEXPENSES", "Exempt Expenses", "0", TaxType.EXEMPT),
        ("RCINPUT", "Reverse Charge Expenses (5%)", "5", TaxType.REVERSE_CHARGE),
        ("NONE", "No VAT", "0", TaxType.NONE),
        ("NONE", "Tax Exempt", "0", TaxType.NONE),  # Xero's code wins over the org's name
        ("INPUT", "Tax on Purchases", "0", None),  # a non-UAE org: no 5% purchase rate
        ("TAX001", "Something at 15%", "15", None),
    ],
)
def test_tax_rates_are_matched_to_our_vat_types(
    code: str, name: str, rate: str, expected: TaxType | None
) -> None:
    assert map_tax_rate(code, name, Decimal(rate), True) is expected


@pytest.mark.parametrize(
    ("status", "says"),
    [(401, "reconnect Xero"), (403, "reconnect Xero"), (429, "request limit"), (500, "HTTP 500")],
)
def test_a_refusal_from_xero_says_what_to_do_without_any_token(status: int, says: str) -> None:
    refusing = httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(status)))

    with pytest.raises(XeroApiError, match=says) as error:
        XeroAccountingSystem("secret-token", "tenant", refusing).fetch_master_data()

    assert "secret-token" not in str(error.value)
