"""Posting a bill to Xero (task 5.5): the sub-steps, what is sent, resuming, and refusals.
All values are made up."""

import json
from collections.abc import Callable
from datetime import date
from decimal import Decimal
from typing import Any

import httpx
import pytest

from app.adapters.accounting.base import (
    PostingBill,
    PostingLine,
    PostingRefused,
    PostingUnavailable,
)
from app.adapters.accounting.xero.client import to_json
from app.adapters.accounting.xero.master_data import XeroAccountingSystem
from app.domain.enums import AmountType

TRN = "100000000000003"
BILL = PostingBill(
    supplier_name="Made-up Trading LLC",
    supplier_tax_number=TRN,
    supplier_external_id=None,
    invoice_number="INV-1",
    invoice_date=date(2026, 5, 4),
    due_date=None,
    currency="AED",
    amount_type=AmountType.EXCLUSIVE,
    lines=[PostingLine("Coffee beans", Decimal("3"), Decimal("12.345"), "429", "INPUT")],
    note="Created by test",
)

Handler = Callable[[httpx.Request], httpx.Response | None]


class FakeXero:
    """Answers like Xero; `override` can answer a request differently."""

    def __init__(self, contacts: list[dict[str, Any]] | None = None) -> None:
        self.contacts = contacts or []
        self.bills: list[dict[str, Any]] = []
        self.requests: list[httpx.Request] = []
        self.override: Handler = lambda _: None

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self._answer))

    def _answer(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if (special := self.override(request)) is not None:
            return special
        path, method = request.url.path.removeprefix("/api.xro/2.0/"), request.method
        if (method, path) == ("GET", "Contacts"):
            where = request.url.params.get("where", "")
            found = [c for c in self.contacts if f'"{c.get("TaxNumber")}"' in where]
            return httpx.Response(200, json={"Contacts": found})
        if (method, path) == ("PUT", "Contacts"):
            return httpx.Response(200, json={"Contacts": [{"ContactID": "new-contact"}]})
        if (method, path) == ("GET", "Invoices"):
            return httpx.Response(200, json={"Invoices": self.bills})
        if (method, path) == ("PUT", "Invoices"):
            return httpx.Response(200, json={"Invoices": [{"InvoiceID": "bill-1", "Total": 38.89}]})
        if method == "PUT" and path.endswith("/History"):
            return httpx.Response(200, json={"HistoryRecords": []})
        return httpx.Response(404, json={"Message": "not found"})

    def sent(self, method: str, path: str) -> list[httpx.Request]:
        return [r for r in self.requests if r.method == method and r.url.path.endswith(path)]


def post(xero: FakeXero, progress: dict[str, Any] | None = None) -> tuple[Any, dict[str, Any]]:
    progress = {} if progress is None else progress
    saved: list[str] = []
    system = XeroAccountingSystem("token", "tenant", xero.client())
    posted = system.post_bill(BILL, key="key-1", progress=progress, save=saved.append)
    return posted, {"progress": progress, "saved": saved}


def test_a_new_supplier_is_created_and_the_bill_sent_awaiting_approval() -> None:
    xero = FakeXero()

    posted, state = post(xero)

    (bill_request,) = xero.sent("PUT", "/Invoices")
    (bill,) = json.loads(bill_request.content)["Invoices"]
    assert bill["Type"] == "ACCPAY" and bill["Status"] == "SUBMITTED"
    assert bill["Contact"] == {"ContactID": "new-contact"}
    assert bill["LineAmountTypes"] == "Exclusive"
    assert bill["LineItems"] == [{"Description": "Coffee beans", "Quantity": 3,
        "UnitAmount": 12.345, "AccountCode": "429", "TaxType": "INPUT"}]  # fmt: skip
    assert b'"UnitAmount": 12.345' in bill_request.content  # exact, never through float
    assert "DueDate" not in bill  # null, never invented
    assert bill_request.headers["Idempotency-Key"] == "key-1"
    assert bill_request.url.params["unitdp"] == "4"
    (contact,) = json.loads(xero.sent("PUT", "/Contacts")[0].content)["Contacts"]
    assert contact == {"Name": "Made-up Trading LLC", "TaxNumber": TRN}
    assert json.loads(xero.sent("PUT", "/bill-1/History")[0].content) == {
        "HistoryRecords": [{"Details": "Created by test"}]}  # fmt: skip
    assert (posted.external_id, posted.total, posted.contact_created) == (
        "bill-1", Decimal("38.89"), True)  # fmt: skip
    assert state["saved"] == ["contact", "checked", "bill", "noted"]


def test_a_supplier_already_in_xero_is_found_by_its_trn() -> None:
    xero = FakeXero(contacts=[{"ContactID": "known", "Name": "Other spelling", "TaxNumber": TRN}])

    posted, _ = post(xero)

    assert (posted.contact_external_id, posted.contact_created) == ("known", False)
    assert xero.sent("PUT", "/Contacts") == []


def test_the_same_bill_already_in_xero_is_refused_before_anything_is_created() -> None:
    xero = FakeXero()
    xero.bills = [{"InvoiceID": "old", "Status": "AUTHORISED"}]

    with pytest.raises(PostingRefused, match="already in Xero \\(authorised\\)"):
        post(xero)

    lookup = xero.sent("GET", "/Invoices")[0].url.params
    assert (lookup["InvoiceNumbers"], lookup["ContactIDs"]) == ("INV-1", "new-contact")
    assert "DELETED" not in lookup["Statuses"] and "VOIDED" not in lookup["Statuses"]
    assert xero.sent("PUT", "/Invoices") == []


def test_after_a_crash_finished_sub_steps_are_not_done_again() -> None:
    xero = FakeXero()

    _, state = post(xero, {"contact_id": "c-9", "contact_created": False, "checked": True})

    assert [(r.method, r.url.path.rsplit("/", 1)[-1]) for r in xero.requests] == [
        ("PUT", "Invoices"), ("PUT", "History")]  # fmt: skip
    assert state["saved"] == ["bill", "noted"]


def test_xero_refusing_the_content_gives_its_reasons() -> None:
    xero = FakeXero()
    xero.override = lambda r: (
        httpx.Response(400, json={"Message": "A validation exception occurred", "Elements": [
            {"ValidationErrors": [{"Message": "Account code '429' is not a valid code"}]}]})
        if r.method == "PUT" and r.url.path.endswith("/Invoices") else None
    )  # fmt: skip

    with pytest.raises(PostingRefused, match=r"^Account code '429' is not a valid code$"):
        post(xero)


@pytest.mark.parametrize(("status", "retry_after"), [(429, 7), (503, None)])
def test_a_busy_or_down_xero_means_try_again_later(status: int, retry_after: int | None) -> None:
    xero = FakeXero()
    headers = {"Retry-After": "7"} if status == 429 else {}
    xero.override = lambda r: httpx.Response(status, headers=headers)

    with pytest.raises(PostingUnavailable) as error:
        post(xero)

    assert error.value.retry_after == retry_after
    assert "token" not in str(error.value)


def test_money_goes_to_json_as_exact_numbers() -> None:
    assert to_json({"a": Decimal("0.10"), "b": [Decimal("-1234567.8901")], "c": "x"}) == (
        '{"a": 0.10, "b": [-1234567.8901], "c": "x"}'
    )
