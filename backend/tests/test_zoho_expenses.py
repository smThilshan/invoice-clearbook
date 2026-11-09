"""Posting an expense to Zoho Books as an expense (decision 89): the sub-steps, what is sent,
resuming, refusals. The fake answers as the Zoho trial did (2026-11-09). All values made up."""

import json
from collections.abc import Callable
from dataclasses import replace
from datetime import date
from decimal import Decimal
from typing import Any

import httpx
import pytest

from app.adapters.accounting.base import (
    CREATE_SENT,
    Attachment,
    PostingAccessDenied,
    PostingExpense,
    PostingLine,
    PostingRefused,
    PostingUnavailable,
)
from app.adapters.accounting.zoho.books import ZohoBooks
from app.adapters.accounting.zoho.expenses import zoho_vat
from app.adapters.accounting.zoho.master_data import NO_VAT
from app.domain.enums import AmountType

KEY = "3f6fcc9c-0000-4000-8000-000000000002"
OURS = KEY.replace("-", "")  # in the reference: Zoho takes fewer than 50 characters
STANDARD, ZERO = "46001", "46002"
RATES = {STANDARD: Decimal(5), ZERO: Decimal(0)}
PETTY_CASH, GROCERIES, OTHER = "4600361", "4600400", "5000"


def line(
    amount: str, account: str = GROCERIES, tax: str = STANDARD, text: str = "Groceries"
) -> PostingLine:
    return PostingLine(text, Decimal(1), Decimal(amount), account, tax)


# Dream Night 72175 as made-up data: items 210.58 with VAT included (VAT 10.03 printed), the
# till's round-off -0.08, total 210.50; sent as one line per account and VAT.
EXPENSE = PostingExpense(
    supplier_name="Made-up Department Store LLC",
    supplier_tax_number="100000000000003",
    supplier_external_id=None,
    reference="72175",
    expense_date=date(2026, 3, 11),
    amount_type=AmountType.INCLUSIVE,
    lines=[line("210.58"), line("-0.08", OTHER, NO_VAT, "Roundoff")],
    paid_through=PETTY_CASH,
    vat=Decimal("10.03"),
    note="Created by test",
)
PDF = Attachment("receipt.pdf", "application/pdf", b"%PDF-1.4 made up")
Handler = Callable[[httpx.Request], httpx.Response | None]


def ok(**body: Any) -> httpx.Response:
    return httpx.Response(200, json={"code": 0, "message": "success", **body})


class FakeZoho:
    """Answers like Zoho Books; `override` can answer a request differently."""

    def __init__(self, vendors: list[dict[str, Any]] | None = None) -> None:
        self.vendors = vendors or []
        self.expenses: list[dict[str, Any]] = []
        self.requests: list[httpx.Request] = []
        self.override: Handler = lambda _: None

    def books(self) -> ZohoBooks:
        http = httpx.Client(transport=httpx.MockTransport(self._answer))
        return ZohoBooks("access-1", "905001", "ae", http)

    def posted(self, path: str) -> list[dict[str, Any]]:
        return [json.loads(r.content) for r in self.requests
                if r.method == "POST" and r.url.path == f"/books/v3/{path}"]  # fmt: skip

    def _answer(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if (special := self.override(request)) is not None:
            return special
        path = request.url.path.removeprefix("/books/v3/")
        params = request.url.params
        if path == "contacts" and request.method == "GET":
            name = params.get("contact_name", "").casefold()
            return ok(contacts=[v for v in self.vendors if v["contact_name"].casefold() == name])
        if path == "contacts":
            vendor = {"contact_id": f"461{len(self.vendors)}", **json.loads(request.content)}
            self.vendors.append(vendor)
            return httpx.Response(201, json={"code": 0, "contact": vendor})
        if path == "chartofaccounts":
            return ok(chartofaccounts=[
                {"account_id": PETTY_CASH, "account_code": ""},
                {"account_id": GROCERIES, "account_code": ""},
                {"account_id": "4600401", "account_code": OTHER},
            ], page_context={"has_more_page": False})  # fmt: skip
        if path == "settings/taxes":
            return ok(taxes=[{"tax_id": STANDARD, "tax_percentage": 5},
                             {"tax_id": ZERO, "tax_percentage": 0}])  # fmt: skip
        if path == "expenses" and request.method == "GET":
            if "reference_number_contains" in params:
                wanted = params["reference_number_contains"]
                return ok(expenses=[e for e in self.expenses if wanted in e["reference_number"]])
            return ok(expenses=[e for e in self.expenses if e["date"] == params["date"]
                                and e.get("vendor_id") == params.get("vendor_id")])  # fmt: skip
        if path == "expenses":
            expense = {"expense_id": f"480{len(self.expenses)}", "total": 210.5,
                       "expense_receipt_name": "", **json.loads(request.content)}  # fmt: skip
            self.expenses.append(expense)
            return httpx.Response(201, json={"code": 0, "expense": expense})
        expense = next(e for e in self.expenses if path.split("/")[1] == e["expense_id"])
        if path.endswith("/receipt"):
            expense["expense_receipt_name"] = "receipt.pdf"
            return httpx.Response(201, json={"code": 0})
        return ok(expense=expense)


def send(
    zoho: FakeZoho, expense: PostingExpense = EXPENSE, progress: dict[str, Any] | None = None
) -> Any:
    progress = {} if progress is None else progress
    return zoho.books().post_expense(expense, key=KEY, progress=progress, save=lambda _: None)


def test_an_expense_goes_paid_through_its_account_with_its_receipt() -> None:
    zoho = FakeZoho()

    posted = send(zoho, replace(EXPENSE, attachment=PDF))

    [body] = zoho.posted("expenses")
    assert body == {
        "account_id": GROCERIES, "date": "2026-03-11", "paid_through_account_id": PETTY_CASH,
        "reference_number": f"72175 {OURS}", "is_inclusive_tax": True,
        "description": "Created by test", "vendor_id": "4610", "tax_treatment": "vat_registered",
        "line_items": [
            {"account_id": GROCERIES, "amount": 210.58, "description": "Groceries",
             "tax_id": STANDARD},
            {"account_id": "4600401", "amount": -0.08, "description": "Roundoff"},
        ],
    }  # fmt: skip
    receipt = next(r for r in zoho.requests if r.url.path.endswith("/receipt"))
    assert b'name="receipt"' in receipt.content  # Zoho's field name for it
    assert (posted.external_id, posted.total, posted.attached) == ("4800", Decimal("210.5"), True)
    assert (posted.contact_external_id, posted.contact_created) == ("4610", True)


def test_vat_zoho_would_round_differently_is_sent_back_before_anything_is_sent() -> None:
    """The trial: 12 till items gave 10.04 for 10.03 printed; as one line, 10.03."""
    items = [line(a) for a in ("22.99", "59.96", "5.44", "0.75", "63.99", "3.99", "13.98", "6.99",
                               "4.25", "24.50", "2.99", "0.75")]  # fmt: skip
    zoho = FakeZoho()

    with pytest.raises(
        PostingRefused, match=r"would work out VAT 10\.04, but the document says 10\.03"
    ):
        send(zoho, replace(EXPENSE, lines=items))

    assert not [r for r in zoho.requests if r.method == "POST"]


def test_zoho_s_vat_is_worked_out_line_by_line() -> None:
    assert zoho_vat(EXPENSE.lines, AmountType.INCLUSIVE, RATES) == Decimal("10.03")
    on_top = [line("100.00"), line("50.00"), line("20.00", tax=ZERO)]
    assert zoho_vat(on_top, AmountType.EXCLUSIVE, RATES) == Decimal("7.50")


def test_an_expense_without_vat_goes_without_a_tax_treatment() -> None:
    zoho = FakeZoho()

    send(zoho, replace(EXPENSE, lines=[line("20.00", tax=NO_VAT)], vat=Decimal(0),
                       amount_type=AmountType.NO_TAX))  # fmt: skip

    [body] = zoho.posted("expenses")
    assert "tax_treatment" not in body and "tax_id" not in body["line_items"][0]


def test_an_expense_naming_no_supplier_goes_without_one() -> None:
    zoho = FakeZoho()

    posted = send(zoho, replace(EXPENSE, supplier_name=None, supplier_tax_number=None))

    [body] = zoho.posted("expenses")
    assert "vendor_id" not in body and not zoho.posted("contacts")
    assert (posted.contact_external_id, posted.contact_created) == (None, False)


def test_without_a_printed_number_the_reference_is_our_key() -> None:
    zoho = FakeZoho()

    send(zoho, replace(EXPENSE, reference=None))

    assert zoho.posted("expenses")[0]["reference_number"] == OURS


def test_the_same_expense_already_in_zoho_is_refused_before_anything_is_created() -> None:
    zoho = FakeZoho([{"contact_id": "4610", "contact_name": "Made-up Department Store LLC"}])
    zoho.expenses.append({"expense_id": "4799", "date": "2026-03-11", "vendor_id": "4610",
                          "total": 210.5, "reference_number": "other"})  # fmt: skip

    with pytest.raises(PostingRefused, match="already in Zoho Books"):
        send(zoho)

    assert not zoho.posted("expenses")


def test_after_a_crash_during_the_create_the_expense_is_found_not_made_twice() -> None:
    zoho = FakeZoho()
    send(zoho)
    progress = {"priced": True, "contact_id": "4610", "contact_created": True, "checked": True,
                CREATE_SENT: True}  # fmt: skip

    posted = send(zoho, progress=progress)

    assert len(zoho.posted("expenses")) == 1
    assert posted.external_id == "4800"


def test_a_retry_finds_its_own_expense_instead_of_calling_it_a_duplicate() -> None:
    zoho = FakeZoho()
    send(zoho)

    posted = send(zoho)  # a new attempt of the same outbox row, from the start

    assert len(zoho.posted("expenses")) == 1
    assert posted.external_id == "4800"


def test_finished_sub_steps_are_not_done_again() -> None:
    zoho = FakeZoho()
    zoho.expenses.append({"expense_id": "4800", "total": 210.5, "expense_receipt_name": ""})
    progress = {"priced": True, "contact_id": "4610", "contact_created": False, "checked": True,
                "expense_id": "4800", "attached": False}  # fmt: skip

    send(zoho, progress=progress)

    assert not [r for r in zoho.requests if r.method == "POST"]


@pytest.mark.parametrize("account", ["paid", "line"])
def test_an_account_no_longer_in_zoho_is_sent_back(account: str) -> None:
    gone_line = [line("210.58", account="gone"), EXPENSE.lines[1]]
    expense = (
        replace(EXPENSE, paid_through="gone") if account == "paid"
        else replace(EXPENSE, lines=gone_line)
    )  # fmt: skip

    with pytest.raises(PostingRefused, match="is not in Zoho Books: sync the master data"):
        send(FakeZoho(), expense)


def test_long_texts_are_cut_to_zoho_s_limit() -> None:
    zoho = FakeZoho()

    send(zoho, replace(EXPENSE, reference="R" * 120, note="N" * 120))

    [body] = zoho.posted("expenses")
    assert len(body["reference_number"]) == 49 and body["reference_number"].endswith(OURS)
    assert len(body["description"]) == 100


def test_a_file_zoho_cannot_take_leaves_the_expense() -> None:
    zoho = FakeZoho()
    zoho.override = lambda r: (httpx.Response(400, json={"code": 2, "message": "not valid"})
                               if r.url.path.endswith("/receipt") else None)  # fmt: skip

    posted = send(zoho, replace(EXPENSE, attachment=PDF))

    assert posted.attached is False and posted.external_id == "4800"


@pytest.mark.parametrize("status", [429, 503])
def test_a_busy_or_down_zoho_means_try_again_later(status: int) -> None:
    zoho = FakeZoho()
    zoho.override = lambda r: httpx.Response(status, json={"code": 1, "message": "busy"})

    with pytest.raises(PostingUnavailable):
        send(zoho)


def test_a_connection_without_the_expense_permission_must_sign_in_again() -> None:
    zoho = FakeZoho()
    zoho.override = lambda r: (httpx.Response(401, json={"code": 57, "message": "not authorized"})
                               if r.url.path.endswith("/expenses") else None)  # fmt: skip

    with pytest.raises(PostingAccessDenied):
        send(zoho)


@pytest.mark.parametrize(
    ("answer", "gone"),
    [(httpx.Response(404, json={"code": 1002, "message": "Expense does not exist."}), True),
     (ok(expense={"expense_id": "4800"}), False)],
)  # fmt: skip
def test_a_deleted_expense_is_gone(answer: httpx.Response, gone: bool) -> None:
    zoho = FakeZoho()
    zoho.override = lambda r: answer

    assert zoho.books().is_deleted("4800", expense=True) is gone
    assert zoho.requests[-1].url.path == "/books/v3/expenses/4800"
