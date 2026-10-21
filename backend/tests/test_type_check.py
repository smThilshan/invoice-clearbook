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


def test_shop_slip_titled_tax_invoice_with_cash_payment_is_a_fine_receipt() -> None:
    assert check(T.RECEIPT, "TAX INVOICE", "Total 90.00 CASH 100.00 CHANGE 10.00") == (set(), False)
