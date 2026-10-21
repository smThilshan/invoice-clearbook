"""Every validation rule: a clean bill passes, then one thing is broken at a time."""

from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from app.core.config import Settings
from app.domain.enums import AmountType, DocumentType
from app.domain.invoice import Invoice, Line
from app.pipeline.verify.issues import Issue, Severity
from app.pipeline.verify.normalize import DIRHAM_SIGN
from app.pipeline.verify.rules import RuleContext, run_rules

FOLDER_DATE = date(2026, 10, 20)
TODAY = date(2026, 10, 21)


def invoice(**changes: Any) -> Invoice:
    fields: dict[str, Any] = {
        "document_type": DocumentType.BILL,
        "vendor_name": "Al Waha Hypermarket LLC",
        "invoice_number": "INV-00291",
        "invoice_date": date(2026, 10, 15),
        "due_date": date(2026, 11, 14),
        "currency": "AED",
        "vendor_trn": "100999000000001",
        "amount_type": AmountType.EXCLUSIVE,
        "subtotal": Decimal("1000.00"),
        "vat": Decimal("50.00"),
        "total": Decimal("1050.00"),
        "lines": [
            Line(description="Rice", qty=Decimal(2), unit_price=Decimal("300.00"),
                 amount=Decimal("600.00")),
            Line(description="Oil", qty=Decimal(4), unit_price=Decimal("100.00"),
                 amount=Decimal("400.00")),
        ],
    }  # fmt: skip
    fields.update(changes)
    return Invoice(**fields)


def context(page_text: str = "TAX INVOICE  Total AED 1,050.00", **changes: Any) -> RuleContext:
    return RuleContext(
        **{"folder_date": FOLDER_DATE, "today": TODAY, "page_text": page_text, **changes}
    )


def issues(inv: Invoice, ctx: RuleContext | None = None) -> list[Issue]:
    return run_rules(inv, ctx or context())


def codes(inv: Invoice, ctx: RuleContext | None = None) -> set[str]:
    return {issue.code for issue in issues(inv, ctx)}


def only(inv: Invoice, code: str, ctx: RuleContext | None = None) -> Issue:
    [issue] = [i for i in issues(inv, ctx) if i.code == code]
    return issue


def test_clean_bill_has_no_issues() -> None:
    assert issues(invoice()) == []


def test_unreadable_extraction_is_one_error() -> None:
    [issue] = run_rules(None, context(), extraction_error="3 schema error(s): total:decimal")

    assert (issue.code, issue.severity) == ("extraction_invalid", Severity.ERROR)
    assert "3 schema error(s)" in issue.message


# --- required to post (decision 17) ------------------------------------------------------


@pytest.mark.parametrize("field", ["vendor_name", "invoice_number", "invoice_date", "total",
                                   "currency"])  # fmt: skip
def test_bill_needs_each_required_field(field: str) -> None:
    issue = only(invoice(**{field: None}), "required_missing")

    assert (issue.severity, issue.field) == (Severity.ERROR, field)
    assert "bill" in issue.message


def test_receipt_without_number_is_only_a_warning() -> None:
    receipt = invoice(document_type=DocumentType.RECEIPT, invoice_number=None, due_date=None)

    issue = only(receipt, "receipt_number_missing")

    assert issue.severity is Severity.WARNING
    assert "required_missing" not in codes(receipt)


def test_receipt_still_needs_total() -> None:
    receipt = invoice(document_type=DocumentType.RECEIPT, total=None)

    assert only(receipt, "required_missing").field == "total"


def test_non_postable_needs_nothing() -> None:
    quote = Invoice(document_type=DocumentType.NON_POSTABLE)

    assert issues(quote) == []


def test_credit_note_without_original_is_warned() -> None:
    credit = invoice(document_type=DocumentType.CREDIT_NOTE)

    assert only(credit, "credit_note_without_original").severity is Severity.WARNING
    assert "credit_note_without_original" not in codes(
        invoice(document_type=DocumentType.CREDIT_NOTE, original_invoice_ref="INV-00250")
    )


# --- totals and line math ----------------------------------------------------------------


def test_totals_within_one_fils_pass() -> None:
    assert "totals_mismatch" not in codes(invoice(total=Decimal("1050.01")))


def test_totals_mismatch_is_an_error() -> None:
    issue = only(invoice(total=Decimal("1060.00")), "totals_mismatch")

    assert (issue.severity, issue.field) == (Severity.ERROR, "total")
    assert "1050.00" in issue.message and "1060.00" in issue.message


def test_line_qty_times_price_mismatch_is_a_warning() -> None:
    lines = [
        Line(description="Rice", qty=Decimal(2), unit_price=Decimal("300.00"),
             amount=Decimal("650.00")),
    ]  # fmt: skip
    inv = invoice(lines=lines, subtotal=Decimal("650.00"), vat=Decimal("32.50"),
                  total=Decimal("682.50"))  # fmt: skip

    issue = only(inv, "line_amount_mismatch")

    assert (issue.severity, issue.field) == (Severity.WARNING, "lines[0].amount")


def test_lines_not_adding_up_to_subtotal_is_a_warning() -> None:
    lines = [Line(description="Rice", amount=Decimal("600.00"))]

    issue = only(invoice(lines=lines), "lines_sum_mismatch")

    assert issue.severity is Severity.WARNING
    assert "discount" in issue.message


def test_inclusive_lines_are_compared_with_the_total() -> None:
    lines = [Line(description="Groceries", amount=Decimal("1050.00"))]

    assert "lines_sum_mismatch" not in codes(invoice(lines=lines, amount_type=AmountType.INCLUSIVE))


def test_lines_without_qty_or_price_are_not_multiplied() -> None:
    lines = [Line(description="Service", amount=Decimal("1000.00"))]

    assert issues(invoice(lines=lines)) == []


# --- VAT ---------------------------------------------------------------------------------


def test_vat_rounded_per_line_is_tolerated() -> None:
    # 3 items rounded separately: VAT 1.53 vs 5% of 30.30 = 1.515
    lines = [Line(description=f"Item {n}", amount=Decimal("10.10")) for n in range(3)]
    inv = invoice(lines=lines, subtotal=Decimal("30.30"), vat=Decimal("1.53"),
                  total=Decimal("31.83"))  # fmt: skip

    assert "vat_rate" not in codes(inv)


def test_vat_not_five_percent_is_an_error() -> None:
    inv = invoice(vat=Decimal("100.00"), total=Decimal("1100.00"))

    issue = only(inv, "vat_rate")

    assert issue.severity is Severity.ERROR
    assert "10.00%" in issue.message


def test_vat_tolerance_comes_from_settings() -> None:
    inv = invoice(vat=Decimal("50.10"), total=Decimal("1050.10"))  # 0.10 off, 2 lines

    assert "vat_rate" in codes(inv, context(vat_tolerance_per_line=Decimal("0.01")))
    assert "vat_rate" not in codes(inv, context(vat_tolerance_per_line=Decimal("0.05")))


def test_zero_vat_from_uae_supplier_with_trn_is_a_warning() -> None:
    inv = invoice(vat=Decimal("0.00"), total=Decimal("1000.00"))

    assert only(inv, "vat_zero").severity is Severity.WARNING


def test_zero_vat_from_foreign_supplier_suggests_reverse_charge_not_error() -> None:
    inv = invoice(vat=Decimal("0.00"), total=Decimal("1000.00"), currency="USD",
                  vendor_trn=None)  # fmt: skip
    found = codes(inv, context(page_text="Total USD 1,000.00"))

    assert "vat_zero_possible_reverse_charge" in found
    assert not any(i.severity is Severity.ERROR for i in issues(inv, context("USD")))


def test_vat_charged_without_supplier_trn_is_warned() -> None:
    assert only(invoice(vendor_trn=None), "vat_without_trn").field == "vendor_trn"


def test_credit_note_with_negative_amounts_passes_vat() -> None:
    lines = [Line(description="Return", amount=Decimal("-1000.00"))]
    credit = invoice(document_type=DocumentType.CREDIT_NOTE, original_invoice_ref="INV-1",
                     lines=lines, subtotal=Decimal("-1000.00"), vat=Decimal("-50.00"),
                     total=Decimal("-1050.00"))  # fmt: skip

    assert issues(credit) == []


# --- TRN -----------------------------------------------------------------------------------


def test_supplier_trn_must_be_15_digits() -> None:
    issue = only(invoice(vendor_trn="10099900000"), "trn_invalid")

    assert (issue.severity, issue.field) == (Severity.ERROR, "vendor_trn")
    assert "found 11" in issue.message


def test_formatted_trn_is_fine() -> None:
    assert "trn_invalid" not in codes(invoice(vendor_trn="100-9990-0000-0001"))


def test_bad_customer_trn_is_only_a_warning() -> None:
    issue = only(invoice(recipient_trn="123"), "trn_invalid")

    assert (issue.severity, issue.field) == (Severity.WARNING, "recipient_trn")


def test_supplier_trn_equal_to_customer_trn_is_an_error() -> None:
    issue = only(invoice(recipient_trn="100-9990-0000-0001"), "trn_is_recipient")

    assert (issue.severity, issue.field) == (Severity.ERROR, "vendor_trn")


def test_different_supplier_and_customer_trn_are_fine() -> None:
    assert "trn_is_recipient" not in codes(invoice(recipient_trn="100222333444555"))


# --- dates and period -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("issued", "code", "severity"),
    [
        (date(1520, 5, 1), "date_implausible", Severity.ERROR),  # "1/5/2026" copied into the year
        (date(1999, 12, 31), "date_implausible", Severity.ERROR),
        (date(2024, 10, 1), "date_very_old", Severity.WARNING),
    ],
)
def test_impossible_or_very_old_dates_are_flagged(
    issued: date, code: str, severity: Severity
) -> None:
    issue = only(invoice(invoice_date=issued, due_date=None), code, context(folder_date=None))

    assert (issue.severity, issue.field) == (severity, "invoice_date")


def test_recent_dates_are_not_flagged_as_old() -> None:
    found = codes(invoice(invoice_date=date(2025, 1, 1), due_date=None), context(folder_date=None))

    assert not found & {"date_implausible", "date_very_old"}


def test_tomorrow_is_allowed_but_later_is_an_error() -> None:
    tomorrow = invoice(invoice_date=date(2026, 10, 22), due_date=None)
    later = invoice(invoice_date=date(2026, 10, 25), due_date=None)

    assert "date_in_future" not in codes(tomorrow)
    assert only(later, "date_in_future").severity is Severity.ERROR


def test_due_before_issue_is_an_error() -> None:
    issue = only(invoice(due_date=date(2026, 10, 1)), "due_before_issue")

    assert (issue.severity, issue.field) == (Severity.ERROR, "due_date")


def test_document_dated_after_its_folder_is_warned() -> None:
    inv = invoice(invoice_date=date(2026, 10, 21), due_date=None)

    assert only(inv, "date_after_folder").severity is Severity.WARNING


@pytest.mark.parametrize(
    ("issued", "warned"),
    [
        (date(2026, 7, 22), False),  # 90 days
        (date(2026, 7, 21), True),
    ],
)  # 91 days
def test_document_long_before_its_folder_is_warned(issued: date, warned: bool) -> None:
    inv = invoice(invoice_date=issued, due_date=None)

    assert ("date_long_before_folder" in codes(inv)) is warned


# --- currency ------------------------------------------------------------------------------


def test_currency_must_be_a_code() -> None:
    issue = only(invoice(currency="Dhs"), "currency_invalid")

    assert issue.severity is Severity.ERROR


def test_currency_disagreeing_with_the_page_is_warned() -> None:
    issue = only(invoice(currency="USD"), "currency_mismatch")  # page shows AED

    assert issue.severity is Severity.WARNING
    assert "AED" in issue.message


def test_dirham_sign_on_page_confirms_aed() -> None:
    assert issues(invoice(), context(page_text=f"Total {DIRHAM_SIGN} 1,050.00")) == []


def test_ocr_reading_of_dirham_sign_is_a_hint_against_other_currencies() -> None:
    ctx = context(page_text="Total D 1,050.00")

    assert issues(invoice(), ctx) == []  # AED agrees with the hint
    assert "probably" in only(invoice(currency="USD"), "currency_mismatch", ctx).message


def test_missing_currency_with_marker_on_page_points_to_it() -> None:
    issue = only(invoice(currency=None), "currency_on_page")

    assert "AED" in issue.message


def test_foreign_currency_is_warned_for_exchange_rate() -> None:
    inv = invoice(currency="USD")
    ctx = context(page_text="Total US$ 1,050.00")

    assert only(inv, "foreign_currency", ctx).severity is Severity.WARNING
    assert "currency_mismatch" not in codes(inv, ctx)


# --- invented values ----------------------------------------------------------------------


def test_original_reference_on_a_bill_is_warned() -> None:
    issue = only(invoice(original_invoice_ref="INV-00250"), "original_ref_on_bill")

    assert issue.severity is Severity.WARNING


# --- settings ------------------------------------------------------------------------------


def test_context_takes_tolerances_from_settings() -> None:
    settings = Settings(
        _env_file=None,
        vat_tolerance_per_line=Decimal("0.02"),
        period_max_days_before=30,
        future_date_slack_days=0,
    )

    ctx = RuleContext.from_settings(settings, folder_date=FOLDER_DATE, today=TODAY, page_text="")

    assert (ctx.vat_tolerance_per_line, ctx.period_max_days_before) == (Decimal("0.02"), 30)
    assert ctx.future_date_slack_days == 0
