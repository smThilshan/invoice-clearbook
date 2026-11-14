"""Posting an expense to Xero as Spend Money (decision 97): what is sent, what is refused before
anything is created, duplicates, resuming, and deleted ones. All values are made up."""

import json
from collections.abc import Callable
from dataclasses import replace
from datetime import date
from decimal import Decimal
from typing import Any

import httpx
import pytest

from app.adapters.accounting.base import (
    CODE_IS_REFERENCE,
    NOT_FOR_LINES,
    PAYS,
    Attachment,
    PostingExpense,
    PostingLine,
    PostingRefused,
    PostingUnavailable,
)
from app.adapters.accounting.registry import PROVIDERS, expenses_blocked_by
from app.adapters.accounting.xero.expenses import xero_vat
from app.adapters.accounting.xero.links import expense_url
from app.adapters.accounting.xero.master_data import XeroAccountingSystem, _account
from app.domain.enums import AccountingProvider, AmountType

TRN = "100000000000003"
PETTY_CASH = "petty-cash-id"  # a bank account without a code: sent by its AccountID
EXPENSE = PostingExpense(
    supplier_name="Made-up Cafe LLC",
    supplier_tax_number=TRN,
    supplier_external_id=None,
    reference=None,
    expense_date=date(2026, 11, 13),
    amount_type=AmountType.EXCLUSIVE,
    lines=[PostingLine("Coffee", Decimal("1"), Decimal("100.00"), "429", "INPUT")],
    paid_through=PETTY_CASH,
    vat=Decimal("5.00"),
    note="Created by test",
    currency="AED",
)

Handler = Callable[[httpx.Request], httpx.Response | None]


class FakeXero:
    """Answers like Xero did in the trial; `override` can answer a request differently."""

    def __init__(self) -> None:
        self.banks: list[dict[str, Any]] = [
            {"AccountID": PETTY_CASH, "Code": "", "Name": "Petty Cash", "Status": "ACTIVE",
             "CurrencyCode": "AED", "Type": "BANK"},
            {"AccountID": "usd-id", "Code": "091", "Name": "USD Account", "Status": "ACTIVE",
             "CurrencyCode": "USD", "Type": "BANK"},
        ]  # fmt: skip
        self.contacts: list[dict[str, Any]] = []
        self.spends: list[dict[str, Any]] = []  # what a BankTransactions search finds
        self.attachments: list[str] = []
        self.requests: list[httpx.Request] = []
        self.override: Handler = lambda _: None

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self._answer))

    def _answer(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if (special := self.override(request)) is not None:
            return special
        path, method = request.url.path.removeprefix("/api.xro/2.0/"), request.method
        if (method, path) == ("GET", "Accounts"):
            return httpx.Response(200, json={"Accounts": self.banks})
        if (method, path) == ("GET", "Organisation"):
            return httpx.Response(200, json={"Organisations": [{"BaseCurrency": "AED"}]})
        if (method, path) == ("GET", "TaxRates"):
            rates = [
                {"TaxType": "INPUT", "EffectiveRate": 5.0},
                {"TaxType": "NONE", "EffectiveRate": 0},
            ]
            return httpx.Response(200, json={"TaxRates": rates})
        if (method, path) == ("GET", "Contacts"):
            where = request.url.params.get("where", "")
            found = [c for c in self.contacts if f'"{c.get("TaxNumber")}"' in where]
            return httpx.Response(200, json={"Contacts": found})
        if (method, path) == ("PUT", "Contacts"):
            return httpx.Response(200, json={"Contacts": [{"ContactID": "new-contact"}]})
        if (method, path) == ("GET", "BankTransactions"):
            return httpx.Response(200, json={"BankTransactions": self.spends})
        if (method, path) == ("PUT", "BankTransactions"):
            return httpx.Response(200, json={"BankTransactions": [
                {"BankTransactionID": "spend-1", "Total": 105.0}]})  # fmt: skip
        if method == "GET" and path.endswith("/Attachments"):
            listed = [{"FileName": name} for name in self.attachments]
            return httpx.Response(200, json={"Attachments": listed})
        if method == "PUT" and "/Attachments/" in path:
            self.attachments.append(path.rsplit("/", 1)[1])
            return httpx.Response(200, json={"Attachments": [{"FileName": "x"}]})
        if method == "PUT" and path.endswith("/History"):
            return httpx.Response(200, json={"HistoryRecords": []})
        return httpx.Response(404, json={"Message": "not found"})

    def sent(self, method: str, path: str) -> list[httpx.Request]:
        return [r for r in self.requests if r.method == method and r.url.path.endswith(path)]


def post(
    xero: FakeXero, expense: PostingExpense = EXPENSE, progress: dict[str, Any] | None = None
) -> tuple[Any, dict[str, Any]]:
    progress = {} if progress is None else progress
    saved: list[str] = []
    system = XeroAccountingSystem("token", "tenant", xero.client())
    posted = system.post_expense(expense, key="key-1", progress=progress, save=saved.append)
    return posted, {"progress": progress, "saved": saved}


def refused(xero: FakeXero, expense: PostingExpense) -> str:
    with pytest.raises(PostingRefused) as caught:
        post(xero, expense)
    assert xero.sent("PUT", "/BankTransactions") == []  # nothing created
    return str(caught.value)


def test_an_expense_goes_as_final_spend_money_from_the_chosen_account() -> None:
    xero = FakeXero()

    posted, state = post(xero)

    (request,) = xero.sent("PUT", "/BankTransactions")
    (spend,) = json.loads(request.content)["BankTransactions"]
    assert spend == {
        "Type": "SPEND",
        "Status": "AUTHORISED",
        "Contact": {"ContactID": "new-contact"},
        "BankAccount": {"AccountID": PETTY_CASH},
        "Date": "2026-11-13",
        "LineAmountTypes": "Exclusive",
        "LineItems": [{"Description": "Coffee", "Quantity": 1, "UnitAmount": 100.00,
                       "AccountCode": "429", "TaxType": "INPUT"}],
    }  # fmt: skip
    assert "IsReconciled" not in spend and "Reference" not in spend  # none printed: none sent
    assert b'"UnitAmount": 100.00' in request.content  # exact, never through float
    assert request.headers["Idempotency-Key"] == "key-1"
    assert request.url.params["unitdp"] == "4"
    assert json.loads(xero.sent("PUT", "/spend-1/History")[0].content) == {
        "HistoryRecords": [{"Details": "Created by test"}]}  # fmt: skip
    assert (posted.external_id, posted.total, posted.contact_created) == (
        "spend-1", Decimal("105.0"), True)  # fmt: skip
    assert state["saved"] == [
        "priced", "contact", "checked", "create_sent", "expense", "attached", "noted"]  # fmt: skip


def test_the_printed_number_is_the_reference() -> None:
    xero = FakeXero()

    post(xero, replace(EXPENSE, reference=" R-77 "))

    (spend,) = json.loads(xero.sent("PUT", "/BankTransactions")[0].content)["BankTransactions"]
    assert spend["Reference"] == "R-77"


def test_an_account_with_a_code_is_found_by_it() -> None:
    xero = FakeXero()
    xero.banks[0]["Code"] = "090"

    post(xero, replace(EXPENSE, paid_through="090"))

    (spend,) = json.loads(xero.sent("PUT", "/BankTransactions")[0].content)["BankTransactions"]
    assert spend["BankAccount"] == {"AccountID": PETTY_CASH}


def test_a_supplier_already_in_xero_is_used() -> None:
    xero = FakeXero()
    xero.contacts = [{"ContactID": "known", "Name": "Other spelling", "TaxNumber": TRN}]

    posted, _ = post(xero)

    assert (posted.contact_external_id, posted.contact_created) == ("known", False)
    assert xero.sent("PUT", "/Contacts") == []


def test_no_supplier_goes_back_to_review_xero_needs_one() -> None:
    reason = refused(FakeXero(), replace(EXPENSE, supplier_name="  ", supplier_tax_number=None))

    assert "without a supplier" in reason


def test_vat_xero_would_work_out_differently_is_never_sent() -> None:
    reason = refused(FakeXero(), replace(EXPENSE, vat=Decimal("5.01")))

    assert "Xero would work out VAT 5.00, but the document says 5.01" in reason


def test_xero_rounds_the_vat_of_each_line() -> None:
    items = [PostingLine("item", Decimal(1), Decimal("0.87"), "429", "INPUT")] * 12
    rates = {"INPUT": Decimal(5), "NONE": Decimal(0)}

    assert xero_vat(items, AmountType.EXCLUSIVE, rates) == Decimal("0.48")  # as in the trial
    one = [PostingLine("x", Decimal(1), Decimal("105.00"), "429", "INPUT")]
    assert xero_vat(one, AmountType.INCLUSIVE, rates) == Decimal("5.00")
    assert xero_vat(one, AmountType.NO_TAX, rates) == Decimal(0)


@pytest.mark.parametrize("status", ["ARCHIVED", None])
def test_an_account_no_longer_active_in_xero_is_refused(status: str | None) -> None:
    xero = FakeXero()
    if status:
        xero.banks[0]["Status"] = status
    else:
        xero.banks.pop(0)

    assert "no longer an active bank account" in refused(xero, EXPENSE)


@pytest.mark.parametrize(
    ("paid_through", "currency", "reason"),
    [
        (PETTY_CASH, "USD", "it is in USD but Petty Cash is in AED"),
        ("usd-id", None, "it is in AED but USD Account is in USD"),  # not printed: the org's own
    ],
)
def test_a_currency_other_than_the_accounts_is_refused(
    paid_through: str, currency: str | None, reason: str
) -> None:
    """Xero would book it on the account in the account's currency without a word (trial)."""
    expense = replace(EXPENSE, paid_through=paid_through, currency=currency)

    assert reason in refused(FakeXero(), expense)


def test_the_accounts_own_currency_is_sent() -> None:
    posted, _ = post(FakeXero(), replace(EXPENSE, paid_through="usd-id", currency="USD"))

    assert posted.external_id == "spend-1"


def test_the_same_suppliers_spend_money_of_that_day_and_total_is_a_duplicate() -> None:
    xero = FakeXero()
    xero.spends = [{"Total": 105.0}]

    assert "already in Xero" in refused(xero, EXPENSE)
    where = xero.sent("GET", "/BankTransactions")[0].url.params["where"]
    assert 'Status!="DELETED"' in where and "Date==DateTime(2026,11,13)" in where
    assert 'Contact.ContactID==guid("new-contact")' in where


def test_another_total_that_day_is_not_a_duplicate() -> None:
    xero = FakeXero()
    xero.spends = [{"Total": 12.5}]

    posted, _ = post(xero)

    assert posted.external_id == "spend-1"


def test_the_same_printed_number_from_the_supplier_is_a_duplicate() -> None:
    xero = FakeXero()

    def by_number(request: httpx.Request) -> httpx.Response | None:
        if request.method == "GET" and 'Reference=="R-77"' in request.url.params.get("where", ""):
            return httpx.Response(200, json={"BankTransactions": [{"Total": 1.0}]})
        return None

    xero.override = by_number

    assert "number R-77" in refused(xero, replace(EXPENSE, reference="R-77"))


def test_a_retry_after_the_create_was_sent_asks_again_with_the_same_key() -> None:
    xero = FakeXero()
    progress = {"priced": True, "bank_id": PETTY_CASH, "contact_id": "c-1",
                "contact_created": False, "checked": True, "create_sent": True}  # fmt: skip

    post(xero, progress=progress)

    (request,) = xero.sent("PUT", "/BankTransactions")
    assert request.headers["Idempotency-Key"] == "key-1"  # Xero returns the first one
    assert xero.sent("GET", "/BankTransactions") == []  # the duplicate check is not repeated


def test_a_retry_after_it_was_created_creates_nothing() -> None:
    xero = FakeXero()
    progress = {"priced": True, "bank_id": PETTY_CASH, "contact_id": "c-1",
                "contact_created": False, "checked": True, "create_sent": True,
                "expense_id": "spend-1", "total": "105.0"}  # fmt: skip

    posted, _ = post(xero, progress=progress)

    assert xero.sent("PUT", "/BankTransactions") == []
    assert posted.external_id == "spend-1"


def test_the_original_is_attached_once() -> None:
    xero = FakeXero()
    receipt = Attachment("receipt.pdf", "application/pdf", b"%PDF-1.4 made up")

    posted, _ = post(xero, replace(EXPENSE, attachment=receipt))

    assert posted.attached and xero.attachments == ["receipt.pdf"]
    assert xero.sent("PUT", "/BankTransactions/spend-1/Attachments/receipt.pdf")


def test_an_original_xero_refuses_leaves_the_expense_and_says_so() -> None:
    xero = FakeXero()
    xero.override = lambda r: (
        httpx.Response(400, json={"Message": "bad file"}) if "/Attachments/" in r.url.path else None
    )
    receipt = Attachment("receipt.heic", "image/heic", b"made up")

    posted, _ = post(xero, replace(EXPENSE, attachment=receipt))

    assert posted.external_id == "spend-1" and not posted.attached
    (note,) = json.loads(xero.sent("PUT", "/History")[0].content)["HistoryRecords"]
    assert note["Details"].endswith("could not be attached: it is in Missan's folders.")


def test_xero_busy_is_tried_again_later() -> None:
    xero = FakeXero()
    xero.override = lambda r: httpx.Response(429, headers={"Retry-After": "30"})

    with pytest.raises(PostingUnavailable):
        post(xero)


@pytest.mark.parametrize(
    ("answer", "gone"),
    [
        (httpx.Response(200, json={"BankTransactions": [{"Status": "DELETED"}]}), True),
        (httpx.Response(200, json={"BankTransactions": [{"Status": "AUTHORISED"}]}), False),
        (httpx.Response(404, json={"Message": "not found"}), True),
    ],
)
def test_a_spend_money_deleted_in_xero_is_seen(answer: httpx.Response, gone: bool) -> None:
    xero = FakeXero()
    xero.override = lambda r: answer if "/BankTransactions/spend-1" in r.url.path else None
    system = XeroAccountingSystem("token", "tenant", xero.client())

    assert system.is_deleted("spend-1", expense=True) is gone
    assert xero.requests[0].url.path.endswith("/BankTransactions/spend-1")


def test_bank_accounts_are_the_accounts_an_expense_is_paid_from() -> None:
    no_code = _account({"AccountID": "a-1", "Code": "", "Name": "Petty Cash", "Class": "ASSET",
                        "Type": "BANK", "BankAccountType": "BANK", "Status": "ACTIVE"})  # fmt: skip
    card = _account({"AccountID": "a-2", "Code": "092", "Name": "Card", "Class": "ASSET",
                     "Type": "BANK", "BankAccountType": "CREDITCARD"})  # fmt: skip
    expense = _account({"AccountID": "a-3", "Code": "429", "Name": "General", "Class": "EXPENSE",
                        "Type": "EXPENSE", "Status": "ACTIVE"})  # fmt: skip

    assert (no_code.code, no_code.raw[PAYS], no_code.raw[CODE_IS_REFERENCE]) == (
        "a-1",
        "Bank",
        True,
    )
    assert no_code.raw[NOT_FOR_LINES] and card.raw[NOT_FOR_LINES]
    assert (card.code, card.raw[PAYS], CODE_IS_REFERENCE in card.raw) == (
        "092",
        "Credit Card",
        False,
    )
    assert (expense.code, expense.raw) == ("429", {"Type": "EXPENSE"})  # as before


def test_xero_takes_expenses_with_the_permission_connections_already_have() -> None:
    xero = PROVIDERS[AccountingProvider.XERO]

    assert expenses_blocked_by(xero, "offline_access accounting.banktransactions") is None
    assert expenses_blocked_by(xero, "offline_access accounting.invoices") == (
        "connect Xero again to allow sending expenses")  # fmt: skip
    assert xero.expense_url is expense_url


def test_a_spend_money_link_opens_the_right_organisation() -> None:
    assert expense_url("!abc", "spend-1") == (
        "https://go.xero.com/organisationlogin/default.aspx?shortcode=%21abc&redirecturl="
        "%2FBank%2FViewTransaction.aspx%3FbankTransactionID%3Dspend-1"
    )
