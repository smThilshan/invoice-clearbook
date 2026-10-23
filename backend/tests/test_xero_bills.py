"""Posting a bill to Xero (task 5.5): the sub-steps, what is sent, resuming, and refusals.
All values are made up."""

import json
from collections.abc import Callable
from dataclasses import replace
from datetime import date
from decimal import Decimal
from typing import Any

import httpx
import pytest

from app.adapters.accounting.base import (
    Attachment,
    PostingBill,
    PostingLine,
    PostingRefused,
    PostingUnavailable,
)
from app.adapters.accounting.xero.bills import MAX_ATTACHMENT_BYTES
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
        self.organisation_terms: dict[str, Any] | None = {"Day": 30, "Type": "DAYSAFTERBILLDATE"}
        self.bills: list[dict[str, Any]] = []
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
        if (method, path) == ("GET", "Contacts"):
            where = request.url.params.get("where", "")
            found = [c for c in self.contacts if f'"{c.get("TaxNumber")}"' in where]
            return httpx.Response(200, json={"Contacts": found})
        if method == "GET" and path.startswith("Contacts/"):
            wanted = path.split("/")[1]
            return httpx.Response(200, json={"Contacts": [
                c for c in self.contacts if c["ContactID"] == wanted]})  # fmt: skip
        if (method, path) == ("GET", "Organisation"):
            terms = {"Bills": self.organisation_terms} if self.organisation_terms else {}
            return httpx.Response(200, json={"Organisations": [{"PaymentTerms": terms}]})
        if (method, path) == ("PUT", "Contacts"):
            return httpx.Response(200, json={"Contacts": [{"ContactID": "new-contact"}]})
        if (method, path) == ("GET", "Invoices"):
            return httpx.Response(200, json={"Invoices": self.bills})
        if (method, path) == ("PUT", "Invoices"):
            return httpx.Response(200, json={"Invoices": [{"InvoiceID": "bill-1", "Total": 38.89}]})
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
    xero: FakeXero, progress: dict[str, Any] | None = None, bill: PostingBill = BILL
) -> tuple[Any, dict[str, Any]]:
    progress = {} if progress is None else progress
    saved: list[str] = []
    system = XeroAccountingSystem("token", "tenant", xero.client())
    posted = system.post_bill(bill, key="key-1", progress=progress, save=saved.append)
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
    assert bill["DueDate"] == "2026-06-03"  # none printed: the organisation's 30 days in Xero
    assert bill_request.headers["Idempotency-Key"] == "key-1"
    assert bill_request.url.params["unitdp"] == "4"
    (contact,) = json.loads(xero.sent("PUT", "/Contacts")[0].content)["Contacts"]
    assert contact == {"Name": "Made-up Trading LLC", "TaxNumber": TRN}
    assert json.loads(xero.sent("PUT", "/bill-1/History")[0].content) == {"HistoryRecords": [
        {"Details": "Created by test No due date was printed: it was set from the organisation "
                    "terms in Xero."}]}  # fmt: skip
    assert (posted.external_id, posted.total, posted.contact_created) == (
        "bill-1", Decimal("38.89"), True)  # fmt: skip
    assert state["saved"] == ["due_date", "contact", "checked", "bill", "attached", "noted"]


def test_a_supplier_already_in_xero_is_found_by_its_trn() -> None:
    xero = FakeXero(contacts=[{"ContactID": "known", "Name": "Other spelling", "TaxNumber": TRN}])

    posted, _ = post(xero)

    assert (posted.contact_external_id, posted.contact_created) == ("known", False)
    assert xero.sent("PUT", "/Contacts") == []


def test_a_printed_due_date_is_used_as_it_is() -> None:
    xero = FakeXero()

    post(xero, bill=replace(BILL, due_date=date(2026, 5, 20)))

    assert json.loads(xero.sent("PUT", "/Invoices")[0].content)["Invoices"][0]["DueDate"] == (
        "2026-05-20"
    )
    assert xero.sent("GET", "/Organisation") == []


def test_the_suppliers_own_terms_come_before_the_organisations() -> None:
    terms = {"Bills": {"Day": 15, "Type": "OFFOLLOWINGMONTH"}}
    xero = FakeXero(contacts=[{"ContactID": "known", "TaxNumber": TRN, "PaymentTerms": terms}])

    posted, _ = post(xero)

    assert json.loads(xero.sent("PUT", "/Invoices")[0].content)["Invoices"][0]["DueDate"] == (
        "2026-06-15"
    )
    assert posted.due_date_from == "supplier terms"


def test_without_a_due_date_or_terms_nothing_is_created_in_xero() -> None:
    xero = FakeXero()
    xero.organisation_terms = None

    with pytest.raises(PostingRefused, match="type the due date"):
        post(xero)

    assert [r for r in xero.requests if r.method == "PUT"] == []


ORIGINAL = Attachment("inv 7.pdf", "application/pdf", b"%PDF-1.4 made-up")


def test_the_original_file_is_attached_to_the_bill_once() -> None:
    xero = FakeXero()

    posted, _ = post(xero, bill=replace(BILL, attachment=ORIGINAL))
    (upload,) = xero.sent("PUT", "/Attachments/inv 7.pdf")
    post(
        xero,
        {
            "due_date": "2026-06-03",
            "due_date_from": "document",
            "contact_id": "c",
            "contact_created": False,
            "checked": True,
            "bill_id": "bill-1",
            "total": "1",
        },
        replace(BILL, attachment=ORIGINAL),
    )  # fmt: skip  (a retry)

    assert posted.attached
    assert (upload.content, upload.headers["Content-Type"]) == (ORIGINAL.content, "application/pdf")
    assert upload.url.raw_path.endswith(b"/Attachments/inv%207.pdf")  # the name, URL-encoded
    assert xero.attachments == ["inv 7.pdf"]  # the retry found it there


@pytest.mark.parametrize("too_big", [False, True])
def test_a_file_xero_will_not_take_leaves_the_bill_and_says_so(too_big: bool) -> None:
    xero = FakeXero()
    xero.override = lambda r: (
        httpx.Response(400, json={"Message": "File type not allowed"})
        if "/Attachments/" in r.url.path else None
    )  # fmt: skip
    content = b"x" * (MAX_ATTACHMENT_BYTES + 1) if too_big else ORIGINAL.content

    posted, _ = post(xero, bill=replace(BILL, attachment=replace(ORIGINAL, content=content)))

    assert (posted.external_id, posted.attached) == ("bill-1", False)
    note = json.loads(xero.sent("PUT", "/History")[0].content)["HistoryRecords"][0]["Details"]
    assert "could not be attached: it is in Missan's folders" in note
    assert len(xero.sent("PUT", "/Attachments/inv 7.pdf")) == (0 if too_big else 1)


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

    done = {"due_date": "2026-06-03", "due_date_from": "document", "contact_id": "c-9",
            "contact_created": False, "checked": True}  # fmt: skip
    _, state = post(xero, done)

    assert [(r.method, r.url.path.rsplit("/", 1)[-1]) for r in xero.requests] == [
        ("PUT", "Invoices"),
        ("PUT", "History"),
    ]  # fmt: skip  (no file to attach here)
    assert state["saved"] == ["bill", "attached", "noted"]


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
