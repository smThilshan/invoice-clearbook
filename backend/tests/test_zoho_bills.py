"""Posting a bill to Zoho Books (decision 74): the sub-steps, what is sent, resuming, refusals.
The fake answers as the Zoho trial did (2026-11-04). All values are made up."""

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
    PostingBill,
    PostingLine,
    PostingRefused,
    PostingUnavailable,
)
from app.adapters.accounting.zoho.books import ZohoBooks
from app.adapters.accounting.zoho.links import bill_url
from app.adapters.accounting.zoho.master_data import NO_VAT
from app.domain.enums import AmountType

TRN = "100000000000003"
KEY = "3f6fcc9c-0000-4000-8000-000000000001"
BILL = PostingBill(
    supplier_name="Made-up Trading LLC",
    supplier_tax_number=TRN,
    supplier_external_id=None,
    invoice_number="INV-1",
    invoice_date=date(2026, 5, 4),
    due_date=None,
    currency="AED",
    amount_type=AmountType.EXCLUSIVE,
    lines=[
        PostingLine("Coffee beans", Decimal("3"), Decimal("12.345"), "4600400", "46001"),
        PostingLine("Delivery", Decimal("1"), Decimal("5.00"), "5000", NO_VAT),
    ],
    note="Created by test",
)
PDF = Attachment("bill.pdf", "application/pdf", b"%PDF-1.4 made up")
Handler = Callable[[httpx.Request], httpx.Response | None]


def ok(**body: Any) -> httpx.Response:
    return httpx.Response(200, json={"code": 0, "message": "success", **body})


class FakeZoho:
    """Answers like Zoho Books; `override` can answer a request differently."""

    def __init__(self, vendors: list[dict[str, Any]] | None = None) -> None:
        self.vendors = vendors or []  # each with contact_id, contact_name, tax_reg_no
        self.bills: list[dict[str, Any]] = []
        self.comments: list[str] = []
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
            if "search_text" in params:
                return ok(
                    contacts=[
                        v for v in self.vendors if v.get("tax_reg_no") == params["search_text"]
                    ]
                )
            name = params.get("contact_name", "").casefold()
            return ok(contacts=[v for v in self.vendors if v["contact_name"].casefold() == name])
        if path.startswith("contacts/"):
            return ok(contact=next(v for v in self.vendors if v["contact_id"] == path[9:]))
        if path == "contacts":
            vendor = {"contact_id": f"461{len(self.vendors)}", **json.loads(request.content)}
            self.vendors.append(vendor)
            return httpx.Response(201, json={"code": 0, "contact": vendor})
        if path == "chartofaccounts":
            return ok(chartofaccounts=[
                {"account_id": "4600400", "account_code": ""},
                {"account_id": "4600401", "account_code": "5000"},
            ], page_context={"has_more_page": False})  # fmt: skip
        if path == "bills" and request.method == "GET":
            asked = [k for k in ("reference_number", "bill_number", "vendor_id") if k in params]
            found = [b for b in self.bills if all(b.get(k) == params[k] for k in asked)]
            return ok(bills=found)
        if path == "bills":
            bill = {"bill_id": f"470{len(self.bills)}", "status": "draft", "total": "47.04",
                    "documents": [], **json.loads(request.content)}  # fmt: skip
            self.bills.append(bill)
            return httpx.Response(201, json={"code": 0, "bill": bill})
        bill = next(b for b in self.bills if path.split("/")[1] == b["bill_id"])
        if path.endswith("/submit"):
            bill["status"] = "pending_approval"
            return ok()
        if path.endswith("/attachment"):
            bill["documents"].append({"file_name": "bill.pdf"})
            return ok()
        if path.endswith("/comments"):
            self.comments.append(json.loads(request.content)["description"])
            return ok()
        return ok(bill=bill)


def send(zoho: FakeZoho, bill: PostingBill = BILL, progress: dict[str, Any] | None = None) -> Any:
    progress = {} if progress is None else progress
    return zoho.books().post_bill(bill, key=KEY, progress=progress, save=lambda _: None)


def test_a_new_supplier_is_created_and_the_bill_submitted_for_approval() -> None:
    zoho = FakeZoho()

    posted = send(zoho, replace(BILL, attachment=PDF))

    [vendor] = zoho.posted("contacts")
    assert vendor == {"contact_name": "Made-up Trading LLC", "contact_type": "vendor",
                      "tax_treatment": "vat_registered", "tax_reg_no": TRN}  # fmt: skip
    [body] = zoho.posted("bills")
    assert body == {
        "vendor_id": "4610", "bill_number": "INV-1", "reference_number": KEY,
        "is_inclusive_tax": False, "date": "2026-05-04",
        "line_items": [
            {"account_id": "4600400", "rate": 12.345, "quantity": 3,
             "description": "Coffee beans", "tax_id": "46001"},
            {"account_id": "4600401", "rate": 5.00, "quantity": 1, "description": "Delivery"},
        ],
    }  # fmt: skip
    assert zoho.bills[0]["status"] == "pending_approval"
    assert (posted.external_id, posted.total, posted.contact_created) == (
        "4700", Decimal("47.04"), True)  # fmt: skip
    assert (posted.attached, posted.due_date_from) == (True, "payment terms in Zoho Books")
    assert zoho.comments == ["Created by test No due date was printed: it was set from the "
                             "payment terms in Zoho Books."]  # fmt: skip


def test_money_goes_as_exact_numbers() -> None:
    zoho = FakeZoho()

    send(zoho)

    raw = next(r.content for r in zoho.requests
               if r.method == "POST" and r.url.path.endswith("/bills"))  # fmt: skip
    assert b'"rate": 12.345' in raw and b'"rate": 5.00' in raw


def test_a_supplier_is_found_by_its_trn_on_its_own_record() -> None:
    zoho = FakeZoho([{"contact_id": "46199", "contact_name": "Other Name LLC", "tax_reg_no": TRN}])

    posted = send(zoho)

    assert (posted.contact_external_id, posted.contact_created) == ("46199", False)
    assert zoho.posted("contacts") == []


def test_a_supplier_whose_name_zoho_says_is_taken_is_used() -> None:
    zoho = FakeZoho()
    taken = {"contact_id": "46150", "contact_name": "Made-up Trading LLC"}

    def name_taken(request: httpx.Request) -> httpx.Response | None:
        if request.method == "POST" and request.url.path.endswith("/contacts"):
            zoho.vendors.append(taken)
            return httpx.Response(400, json={"code": 3062, "message": "already exists"})
        return None

    zoho.override = name_taken
    posted = send(zoho, replace(BILL, supplier_tax_number=None))

    assert posted.contact_external_id == "46150"


def test_a_printed_due_date_and_vat_included_go_as_they_are() -> None:
    zoho = FakeZoho()

    posted = send(zoho, replace(BILL, due_date=date(2026, 6, 3), amount_type=AmountType.INCLUSIVE))

    [body] = zoho.posted("bills")
    assert (body["due_date"], body["is_inclusive_tax"]) == ("2026-06-03", True)
    assert posted.due_date_from == "document"


def test_the_same_bill_already_in_zoho_is_refused_before_anything_is_created() -> None:
    zoho = FakeZoho([{"contact_id": "46199", "contact_name": "Made-up Trading LLC"}])
    zoho.bills.append({"bill_id": "4699", "bill_number": "INV-1", "vendor_id": "46199",
                       "reference_number": "another-row", "status": "open"})  # fmt: skip

    with pytest.raises(PostingRefused, match="INV-1 from this supplier is already in Zoho"):
        send(zoho, replace(BILL, supplier_tax_number=None))

    assert zoho.posted("bills") == []


def test_after_a_crash_during_the_create_the_bill_is_found_not_made_twice() -> None:
    zoho = FakeZoho()
    zoho.bills.append({"bill_id": "4690", "reference_number": KEY, "status": "draft",
                       "total": "47.04", "documents": []})  # fmt: skip
    progress = {"contact_id": "46199", "contact_created": True, "checked": True, CREATE_SENT: True}

    posted = send(zoho, progress=progress)

    assert posted.external_id == "4690"
    assert zoho.posted("bills") == []


def test_finished_sub_steps_are_not_done_again() -> None:
    zoho = FakeZoho()
    progress: dict[str, Any] = {}
    send(zoho, progress=progress)
    requests = len(zoho.requests)

    send(zoho, progress=progress)

    assert [r.url.path for r in zoho.requests[requests:]] == ["/books/v3/bills/4700"]  # the total
    assert len(zoho.bills) == 1


def test_without_bill_approvals_the_bill_stays_a_draft_and_says_so() -> None:
    zoho = FakeZoho()
    zoho.override = lambda r: (
        httpx.Response(400, json={"code": 1001, "message": "Approval is not enabled"})
        if r.url.path.endswith("/submit")
        else None
    )

    send(zoho)

    assert zoho.bills[0]["status"] == "draft"
    assert "Left as a draft" in zoho.comments[0]


def test_a_file_zoho_cannot_take_leaves_the_bill_and_says_so() -> None:
    zoho = FakeZoho()

    posted = send(zoho, replace(BILL, attachment=Attachment("a.heic", "image/heic", b"x")))

    assert posted.attached is False
    assert "could not be attached" in zoho.comments[0]


def test_a_receipt_without_a_number_is_sent_back_before_anything_is_sent() -> None:
    zoho = FakeZoho()

    with pytest.raises(PostingRefused, match="needs a bill number"):
        send(zoho, replace(BILL, invoice_number=None))

    assert zoho.requests == []


def test_an_account_no_longer_in_zoho_is_sent_back_with_the_line() -> None:
    zoho = FakeZoho()
    bill = replace(BILL, lines=[PostingLine("x", Decimal(1), Decimal(1), "9999", "46001")])

    with pytest.raises(PostingRefused, match="line 1: account 9999 is not in Zoho Books"):
        send(zoho, bill)


def test_zoho_refusing_the_content_gives_its_message() -> None:
    zoho = FakeZoho()
    zoho.override = lambda r: (
        httpx.Response(400, json={"code": 13011, "message": "A bill with this number exists"})
        if r.method == "POST" and r.url.path.endswith("/bills")
        else None
    )

    with pytest.raises(PostingRefused, match="A bill with this number exists"):
        send(zoho)


@pytest.mark.parametrize("status", [429, 500])
def test_a_busy_or_down_zoho_means_try_again_later(status: int) -> None:
    zoho = FakeZoho()
    zoho.override = lambda r: httpx.Response(status, json={"code": 44, "message": "busy"})

    with pytest.raises(PostingUnavailable):
        send(zoho)


@pytest.mark.parametrize(("status", "reconnect"), [(401, False), (403, True)])
def test_a_refused_access_says_if_a_new_sign_in_is_needed(status: int, reconnect: bool) -> None:
    zoho = FakeZoho()
    zoho.override = lambda r: httpx.Response(status, json={"code": 57, "message": "no"})

    with pytest.raises(PostingAccessDenied) as caught:
        send(zoho)

    assert caught.value.reconnect is reconnect


def test_a_sent_bill_links_to_its_organisation_in_its_data_centre() -> None:
    assert bill_url("ae:905001", "4700") == "https://books.zoho.ae/app/905001#/bills/4700"
    assert bill_url(None, "4700") is None
    assert bill_url("evil.com:905001", "4700") is None


@pytest.mark.parametrize(
    ("answer", "gone"),
    [
        (httpx.Response(404, json={"code": 1002, "message": "Bill does not exist."}), True),
        (ok(bill={"bill_id": "4700", "status": "void"}), True),
        (ok(bill={"bill_id": "4700", "status": "pending_approval"}), False),
    ],
)
def test_a_deleted_or_voided_bill_is_gone(answer: httpx.Response, gone: bool) -> None:
    zoho = FakeZoho()
    zoho.override = lambda _: answer

    assert zoho.books().is_deleted("4700") is gone


def test_asking_a_busy_zoho_if_a_bill_is_gone_means_try_later() -> None:
    zoho = FakeZoho()
    zoho.override = lambda _: httpx.Response(429, json={"code": 44, "message": "busy"})

    with pytest.raises(PostingUnavailable):
        zoho.books().is_deleted("4700")
