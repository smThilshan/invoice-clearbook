"""Zoho Books master data (decision 74) from a fake Zoho: no network."""

from decimal import Decimal
from typing import Any

import httpx
import pytest

from app.adapters.accounting.base import (
    CODE_IS_REFERENCE,
    NOT_FOR_LINES,
    PAYS,
    AccountingSystemError,
)
from app.adapters.accounting.zoho.books import ZohoBooks
from app.adapters.accounting.zoho.client import ZohoApiError, ZohoClient
from app.adapters.accounting.zoho.master_data import NO_VAT, account_class, map_tax
from app.domain.enums import AccountClass, TaxType

ACCOUNTS = [
    {"account_id": "4600000000000400", "account_name": "Office Supplies",
     "account_type": "expense", "account_code": "", "is_active": True},
    {"account_id": "4600000000000401", "account_name": "Cost of Goods Sold",
     "account_type": "cost_of_goods_sold", "account_code": "5000", "is_active": True},
    {"account_id": "4600000000000402", "account_name": "Old Rent",
     "account_type": "expense", "account_code": None, "is_active": False},
    {"account_id": "4600000000000361", "account_name": "Petty Cash",
     "account_type": "cash", "account_code": "", "is_active": True},
]  # fmt: skip
TAXES = [
    {"tax_id": "46001", "tax_name": "Standard Rate", "tax_percentage": 5, "tax_type": "tax"},
    {"tax_id": "46002", "tax_name": "Zero Rate", "tax_percentage": 0, "tax_type": "tax"},
    {"tax_id": "46003", "tax_name": "Group", "tax_percentage": 5, "tax_type": "tax_group"},
]
VENDORS = [{"contact_id": "46100", "contact_name": "Al Shahama Supermarket", "status": "active"}]


class FakeZoho:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        page = int(request.url.params.get("page", "1"))
        path = request.url.path.removeprefix("/books/v3/")
        if path == "chartofaccounts":  # two pages, to prove every page is read
            rows = ACCOUNTS[:2] if page == 1 else ACCOUNTS[2:]
            return ok({"chartofaccounts": rows, "page_context": {"has_more_page": page == 1}})
        if path == "settings/taxes":
            return ok({"taxes": TAXES})
        if path == "contacts":
            return ok({"contacts": VENDORS, "page_context": {"has_more_page": False}})
        return httpx.Response(404, json={"code": 5, "message": "Invalid URL Passed"})


def ok(body: dict[str, Any]) -> httpx.Response:
    return httpx.Response(200, json={"code": 0, "message": "success", **body})


def books(handler: Any) -> ZohoBooks:
    http = httpx.Client(transport=httpx.MockTransport(handler))
    return ZohoBooks("access-1", "905001", "ae", http)


def test_master_data_comes_in_as_neutral_records() -> None:
    zoho = FakeZoho()

    data = books(zoho).fetch_master_data()

    assert data.accounts is not None and data.tax_rates is not None and data.contacts is not None
    office, cogs, rent, petty = data.accounts
    assert (office.code, office.raw[CODE_IS_REFERENCE]) == ("4600000000000400", True)
    assert (cogs.code, cogs.raw[CODE_IS_REFERENCE]) == ("5000", False)
    assert (office.account_class, cogs.account_class) == (AccountClass.EXPENSE,) * 2
    assert rent.is_active is False
    assert (office.raw[NOT_FOR_LINES], petty.raw[NOT_FOR_LINES]) == (False, True)
    assert [(t.code, t.tax_type) for t in data.tax_rates] == [
        ("46001", TaxType.STANDARD_5), ("46002", TaxType.ZERO), ("46003", None),
        (NO_VAT, TaxType.NONE),
    ]  # fmt: skip
    assert [(c.name, c.is_supplier) for c in data.contacts] == [("Al Shahama Supermarket", True)]
    assert data.organisation_ref == "ae:905001"  # data centre and organisation, for links


def test_every_call_names_the_organisation_and_goes_to_its_data_centre() -> None:
    zoho = FakeZoho()

    books(zoho).fetch_master_data()

    assert {r.url.host for r in zoho.requests} == {"www.zohoapis.ae"}
    assert {r.url.params["organization_id"] for r in zoho.requests} == {"905001"}
    assert {r.headers["authorization"] for r in zoho.requests} == {"Zoho-oauthtoken access-1"}
    assert [r.url.params.get("page") for r in zoho.requests if "chartofaccounts" in r.url.path] == [
        "1",
        "2",
    ]


@pytest.mark.parametrize(
    ("status", "temporary", "access"),
    [(429, True, None), (401, True, 401), (403, True, 403), (500, True, None), (400, False, None)],
)
def test_a_refusal_says_what_it_means(status: int, temporary: bool, access: int | None) -> None:
    def refused(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"code": 57, "message": "Not authorized"})

    client = ZohoClient("a", "905001", "com", httpx.Client(transport=httpx.MockTransport(refused)))

    with pytest.raises(ZohoApiError) as caught:
        client.get("settings/taxes")

    assert (caught.value.temporary, caught.value.access) == (temporary, access)
    assert "Zoho-oauthtoken" not in str(caught.value)


def test_a_refused_sync_reaches_the_accountant_in_words() -> None:
    def refused(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"code": 6024, "message": "Organization not found"})

    with pytest.raises(AccountingSystemError, match="Organization not found"):
        books(refused).fetch_master_data()


def test_zoho_account_types_and_taxes_map_to_canonical_ones() -> None:
    assert account_class("other_expense") is AccountClass.EXPENSE
    assert account_class("accounts_payable") is AccountClass.LIABILITY
    assert account_class("income") is AccountClass.REVENUE
    assert account_class("fixed_asset") is AccountClass.ASSET
    assert map_tax("Standard Rate", Decimal(5)) is TaxType.STANDARD_5
    assert map_tax("VAT Exempt", Decimal(0)) is TaxType.EXEMPT
    assert map_tax("Reverse Charge 5%", Decimal(5)) is TaxType.REVERSE_CHARGE
    assert map_tax("Excise 50%", Decimal(50)) is None


# Zoho's "Paid Through" list and its refusals in the trial (decision 89): name, type, placeholder,
# the group it is offered under (None = never paid through).
PAYMENT_ACCOUNTS = [
    ("Petty Cash", "cash", "", "Cash"),
    ("Emirates NBD Current", "bank", "", "Bank"),
    ("Company Visa", "credit_card", "", "Credit Card"),
    ("Employee Advance", "other_current_asset", "", "Other Current Asset"),
    ("Employee Reimbursements", "other_current_liability", "", "Other Current Liability"),
    ("Owner's Equity", "equity", "", "Equity"),
    ("Input VAT", "other_current_asset", "gl_input_vat", None),  # Zoho refuses it
    ("VAT Payable", "other_current_liability", "gl_vat_payable", None),
    ("Office Supplies", "expense", "", None),
    ("Accounts Payable", "accounts_payable", "", None),
    ("Furniture and Equipment", "fixed_asset", "", None),
]


def test_the_accounts_an_expense_may_be_paid_through_carry_zohos_group() -> None:
    rows = [{"account_id": str(n), "account_name": name, "account_type": kind,
             "placeholder": placeholder, "is_active": True}
            for n, (name, kind, placeholder, _) in enumerate(PAYMENT_ACCOUNTS)]  # fmt: skip

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("chartofaccounts"):
            return ok({"chartofaccounts": rows, "page_context": {"has_more_page": False}})
        return FakeZoho()(request)

    accounts = books(handler).fetch_master_data().accounts or []

    assert [(a.name, a.raw[PAYS]) for a in accounts] == [
        (name, group) for name, _, _, group in PAYMENT_ACCOUNTS
    ]
