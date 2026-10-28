import pytest

from app.domain.enums import DocumentType as T
from app.domain.invoice import Invoice
from app.pipeline.read import PageText, TextSpan
from app.pipeline.verify.issues import Severity
from app.pipeline.verify.type_check import check_document_type, title_signals


def page(title: str, body: str = "Rice 5kg 90.00") -> PageText:
    """A first page: `title` at the top (inside the title area), `body` lower down."""
    spans = [TextSpan(title, (0.1, 0.05, 0.9, 0.1), None),
             TextSpan(body, (0.1, 0.60, 0.9, 0.65), None)]  # fmt: skip
    return PageText(1, 1000, 1400, "ocr", spans)


def check(model: T, title: str, body: str = "Rice 5kg 90.00 Total 90.00") -> tuple[set[str], bool]:
    result = check_document_type(Invoice(document_type=model), [page(title, body)])
    return {issue.code for issue in result.issues}, result.park


# --- title signals ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("TAX INVOICE", {T.BILL}),
        ("فاتورة ضريبية", {T.BILL}),
        ("CREDIT NOTE", {T.CREDIT_NOTE}),
        ("Credit Memo No. 7", {T.CREDIT_NOTE}),
        ("إشعار دائن", {T.CREDIT_NOTE}),
        ("DEBIT NOTE", {T.DEBIT_NOTE}),
        ("QUOTATION", {T.NON_POSTABLE}),
        ("Pro-Forma Invoice", {T.NON_POSTABLE, T.BILL}),
        ("PURCHASE ORDER", {T.NON_POSTABLE}),
        ("Statement of Account", {T.NON_POSTABLE}),
        ("Delivery Note", {T.NON_POSTABLE}),
        ("عرض سعر", {T.NON_POSTABLE}),
        ("CASH RECEIPT", {T.RECEIPT}),
        ("AL WAHA HYPERMARKET", set()),
    ],
)
def test_title_signals(title: str, expected: set[T]) -> None:
    assert title_signals([page(title)]) == expected


def test_only_the_title_area_counts() -> None:
    small_print = "Returns: a credit note will be issued within 7 days."

    assert title_signals([page("TAX INVOICE", small_print)]) == {T.BILL}


def test_no_pages_no_signals() -> None:
    assert title_signals([]) == set()


# --- outcomes -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("model", "title"),
    [
        (T.BILL, "TAX INVOICE"),
        (T.BILL, "AL WAHA HYPERMARKET"),  # no title: nothing contradicts the model
        (T.CREDIT_NOTE, "CREDIT NOTE"),
        (T.CREDIT_NOTE, "AL WAHA HYPERMARKET"),
    ],
)
def test_agreement_has_no_issues(model: T, title: str) -> None:
    assert check(model, title) == (set(), False)


def test_non_postable_confirmed_by_title_is_parked() -> None:
    assert check(T.NON_POSTABLE, "QUOTATION") == (set(), True)


@pytest.mark.parametrize(
    ("model", "title"),
    [
        (T.NON_POSTABLE, "TAX INVOICE"),  # model: quote; title: invoice
        (T.NON_POSTABLE, "AL WAHA HYPERMARKET"),  # model: quote; nothing confirms it
        (T.BILL, "QUOTATION"),  # must never be posted by accident
        (T.BILL, "CREDIT NOTE"),
        (T.RECEIPT, "CREDIT NOTE"),
        (T.CREDIT_NOTE, "TAX INVOICE"),
    ],
)
def test_conflicts_are_errors_never_parked(model: T, title: str) -> None:
    result = check_document_type(Invoice(document_type=model), [page(title)])

    [issue] = result.issues
    assert (issue.code, issue.severity, issue.field) == ("type_conflict", Severity.ERROR,
                                                         "document_type")  # fmt: skip
    assert result.park is False


@pytest.mark.parametrize("model", [T.DEBIT_NOTE, T.SALES_INVOICE])
def test_out_of_scope_types_are_errors(model: T) -> None:
    assert check(model, "DEBIT NOTE") == ({"type_out_of_scope"}, False)


def test_bill_that_shows_a_payment_may_be_a_receipt() -> None:
    codes, _ = check(T.BILL, "TAX INVOICE", "Total 90.00 VISA ****1234 PAID")

    assert codes == {"type_maybe_receipt"}


def test_receipt_without_any_payment_may_be_a_bill() -> None:
    codes, _ = check(T.RECEIPT, "TAX INVOICE", "Total 90.00 Payment due in 30 days")

    assert codes == {"type_maybe_bill"}


@pytest.mark.parametrize(
    ("model", "body", "offered"),
    [
        (T.BILL, "Total 90.00 VISA ****1234 PAID", T.RECEIPT),
        (T.RECEIPT, "Total 90.00 Payment due in 30 days", T.BILL),
    ],
)
def test_bill_or_receipt_offers_the_other_type_with_one_click(
    model: T, body: str, offered: T
) -> None:
    result = check_document_type(Invoice(document_type=model), [page("TAX INVOICE", body)])

    [issue] = result.issues
    assert (issue.severity, issue.suggestion) == (Severity.WARNING, offered)


def test_shop_slip_titled_tax_invoice_with_cash_payment_is_a_fine_receipt() -> None:
    assert check(T.RECEIPT, "TAX INVOICE", "Total 90.00 CASH 100.00 CHANGE 10.00") == (set(), False)


@pytest.mark.parametrize(("model", "park"), [(T.BILL, False), (T.NON_POSTABLE, True)])
def test_type_confirmed_in_review_is_not_questioned_by_the_title(model: T, park: bool) -> None:
    title = "QUOTATION" if model is T.BILL else "TAX INVOICE"
    result = check_document_type(Invoice(document_type=model), [page(title)], confirmed=True)

    assert (result.issues, result.park) == ([], park)


def test_confirmed_out_of_scope_type_is_still_an_error() -> None:
    result = check_document_type(Invoice(document_type=T.SALES_INVOICE), [page("INVOICE")],
                                 confirmed=True)  # fmt: skip

    assert [issue.code for issue in result.issues] == ["type_out_of_scope"]


# --- the type the document shows, offered (decision 48) -----------------------------------

TALABAT = ("Subtotal D 98.87 Delivery fee D 0.00 Service fee D 2.97 Total D 101.84 "
           "Payment method Cash Delivery time 25 mins")  # fmt: skip
NOON = ("Payment Card ending in 0011 Confirmed (1 item) Subtotal (1 Item) D 18.33 "
        "Delivery Fee D 6 Small Order Fee D 3.00 Amount Paid D 27.33")  # fmt: skip


def suggested(model: T, title: str, body: str) -> str | None:
    [issue] = check_document_type(Invoice(document_type=model), [page(title, body)]).issues
    assert (issue.code, issue.severity) == ("type_conflict", Severity.ERROR)  # still flagged
    return issue.suggestion


@pytest.mark.parametrize(
    ("title", "body"),
    [
        ("Order Details talabat mart Order ID: 3526727111", TALABAT),
        ("noon MINUTES Receiver Details Proof of delivery", NOON),
        ("Trip details", "Fare AED 22.73 Payment details Apple Pay AED 22.73"),
        ("Your order", "Total 45.00 Paid with Google Pay"),
    ],
)
def test_app_order_screen_read_as_non_postable_offers_receipt(title: str, body: str) -> None:
    assert suggested(T.NON_POSTABLE, title, body) == "RECEIPT"


def test_non_postable_without_payment_offers_the_titled_type() -> None:
    assert suggested(T.NON_POSTABLE, "TAX INVOICE", "Total 90.00 due in 30 days") == "BILL"


def test_non_postable_with_nothing_to_go_on_offers_nothing() -> None:
    assert suggested(T.NON_POSTABLE, "AL WAHA HYPERMARKET", "Rice 5kg 90.00") is None


@pytest.mark.parametrize(
    "title", ["QUOTATION", "PURCHASE ORDER", "Statement of Account", "Delivery Note",
              "Remittance Advice"],
)  # fmt: skip
def test_titled_non_postables_are_still_parked_even_when_they_mention_payment(title: str) -> None:
    """A statement lists payments and a remittance is about one: the title decides."""
    assert check(T.NON_POSTABLE, title, "Payment received, paid by card 90.00") == (set(), True)


def test_an_order_screen_read_as_a_receipt_has_no_issue() -> None:
    assert check(T.RECEIPT, "Order Details talabat mart", TALABAT) == (set(), False)


@pytest.mark.parametrize("mark", ["Card ending in 0011", "Apple Pay", "GOOGLE PAY", "Samsung Pay"])
def test_app_payment_marks_count_as_payments(mark: str) -> None:
    assert check(T.RECEIPT, "AL WAHA HYPERMARKET", f"Total 90.00 {mark}") == (set(), False)
