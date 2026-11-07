"""Every validation rule: a clean bill passes, then one thing is broken at a time."""

from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from app.core.config import Settings
from app.domain.enums import AccountingProvider, AmountType, DocumentType, TaxType
from app.domain.invoice import Invoice, Line
from app.pipeline.verify.issues import Issue, Severity
from app.pipeline.verify.normalize import DIRHAM_SIGN
from app.pipeline.verify.rules import RuleContext, run_rules
from app.pipeline.verify.step import require_number

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


def test_expense_without_number_is_fine_unless_every_bill_needs_one() -> None:
    """Decision 88: an expense needs no number; Zoho takes no bill without one (decision 74)."""
    receipt = invoice(document_type=DocumentType.RECEIPT, invoice_number=None, due_date=None)

    zoho = require_number(issues(receipt), AccountingProvider.ZOHO)
    xero = require_number(issues(receipt), AccountingProvider.XERO)

    [needed] = [i for i in zoho if i.code == "receipt_number_needed"]
    assert (needed.severity, needed.field) == (Severity.ERROR, "invoice_number")
    assert "Zoho Books needs one" in needed.message
    assert "receipt_number_missing" not in {i.code for i in zoho}
    assert {i.code for i in xero} == codes(receipt) - {"receipt_number_missing"}  # nothing said


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


def test_a_subtotal_read_wrong_raises_nothing() -> None:
    """Decision 81: the subtotal is worked out from the total and the VAT, never checked."""
    assert codes(invoice(subtotal=Decimal("1050.00"))) == codes(invoice())


def test_a_total_read_wrong_is_still_an_error() -> None:
    """Without "subtotal + VAT = total": the VAT is no longer 5% of the total less the VAT,
    and the lines no longer add up to it."""
    found = {i.code: i for i in issues(invoice(total=Decimal("1060.00")))}

    assert found["vat_rate"].severity is Severity.ERROR
    assert "1010.00" in found["vat_rate"].message
    assert found["lines_sum_mismatch"].severity is Severity.ERROR


def test_line_qty_times_price_mismatch_is_a_warning() -> None:
    lines = [
        Line(description="Rice", qty=Decimal(2), unit_price=Decimal("300.00"),
             amount=Decimal("650.00")),
    ]  # fmt: skip
    inv = invoice(lines=lines, subtotal=Decimal("650.00"), vat=Decimal("32.50"),
                  total=Decimal("682.50"))  # fmt: skip

    issue = only(inv, "line_amount_mismatch")

    assert (issue.severity, issue.field) == (Severity.WARNING, "lines[0].amount")


# A line is missing, and the two left are on different accounts: they cannot be one total line.
SHORT_LINES = [
    Line(description="Rice", amount=Decimal("400.00"), account_code="429"),
    Line(description="Oil", amount=Decimal("200.00"), account_code="310"),
]


def test_lines_not_adding_up_to_subtotal_is_an_error() -> None:
    """Decision 82: the accounting system builds the bill from the lines, so its total would
    not be the printed one."""
    issue = only(invoice(lines=SHORT_LINES), "lines_sum_mismatch")

    assert issue.severity is Severity.ERROR
    assert "discount" in issue.message


def test_lines_not_adding_up_are_a_warning_when_the_totals_can_be_sent_as_one_line() -> None:
    """Decision 86: 5% of 1000.00 is the 50.00 printed, so the bill can give the printed total
    and VAT exactly; the accountant sees how it will be sent before approving."""
    lines = [Line(description="Rice", amount=Decimal("600.00"))]

    found = {i.code: i for i in issues(invoice(lines=lines, amount_type=None))}

    assert "lines_sum_mismatch" not in found
    assert "vat_type_missing" not in found  # the totals decide, not the lines' VAT type
    assert (found["total_line"].severity, found["total_line"].field) == (Severity.WARNING, "lines")
    assert "one line: 1000.00 + 50.00 VAT = 1050.00" in found["total_line"].message
    assert "item by item" in found["total_line"].message


def test_the_total_line_message_says_when_the_vat_is_included() -> None:
    lines = [Line(description="Tea", amount=Decimal("9.00"))]
    inv = invoice(lines=lines, subtotal=None, vat=Decimal("0.48"), total=Decimal("10.18"))

    assert "one line: 10.18 including 0.48 VAT" in only(inv, "total_line").message


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


def test_document_dated_after_its_folder_s_month_is_warned() -> None:
    inv = invoice(invoice_date=date(2026, 11, 1), due_date=None)

    assert only(inv, "date_after_folder").severity is Severity.WARNING


def test_document_dated_later_in_its_folder_s_month_is_not_warned() -> None:
    """Decision 85: a receipt of the 21st filed in the day folder of the 20th."""
    inv = invoice(invoice_date=date(2026, 10, 21), due_date=None)

    assert "date_after_folder" not in codes(inv)


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


# --- VAT type (decision 34) --------------------------------------------------------------


def test_vat_type_that_contradicts_the_amounts_is_a_warning_with_the_right_one() -> None:
    issue = only(invoice(amount_type=AmountType.INCLUSIVE), "vat_type_mismatch")

    assert (issue.severity, issue.field) == (Severity.WARNING, "amount_type")
    assert issue.suggestion == "EXCLUSIVE"
    assert "before VAT" in issue.message


def test_vat_type_mismatch_replaces_the_lines_warning() -> None:
    assert "lines_sum_mismatch" not in codes(invoice(amount_type=AmountType.INCLUSIVE))


def test_missing_vat_type_with_vat_is_an_error_with_a_suggestion() -> None:
    issue = only(invoice(amount_type=None), "vat_type_missing")

    assert (issue.severity, issue.suggestion) == (Severity.ERROR, "EXCLUSIVE")


def test_missing_vat_type_the_amounts_cannot_explain_has_no_suggestion() -> None:
    issue = only(invoice(amount_type=None, lines=SHORT_LINES), "vat_type_missing")

    assert issue.suggestion is None
    assert "could not be worked out" in issue.message


def test_missing_vat_type_without_vat_is_fine() -> None:
    lines = [Line(description="Water", amount=Decimal("1000.00"))]
    inv = invoice(amount_type=None, vat=None, subtotal=None, total=Decimal("1000.00"),
                  lines=lines)  # fmt: skip

    assert not codes(inv) & {"vat_type_missing", "vat_type_mismatch"}


def test_vat_type_is_not_checked_without_lines() -> None:
    assert not codes(invoice(amount_type=None, lines=[])) & {"vat_type_missing"}


def test_zero_vat_without_a_supplier_vat_registration_no_says_vat_cannot_be_reclaimed() -> None:
    issue = only(invoice(vat=Decimal("0.00"), total=Decimal("1000.00"), vendor_trn=None),
                 "vat_zero_no_tax_invoice")  # fmt: skip

    assert issue.severity is Severity.WARNING
    assert "no input VAT can be reclaimed" in issue.message


# --- line VAT codes (decision 53) ---------------------------------------------------------


def coded(*tax_types: TaxType) -> list[Line]:
    return [Line(description=f"Item {n}", amount=Decimal("50.00"), tax_type=tax)
            for n, tax in enumerate(tax_types)]  # fmt: skip


def no_vat_receipt(lines: list[Line]) -> Invoice:
    return invoice(document_type=DocumentType.RECEIPT, vendor_trn=None, vat=None,
                   amount_type=AmountType.NO_TAX, subtotal=Decimal("100.00"),
                   total=Decimal("100.00"), lines=lines)  # fmt: skip


def test_a_5_percent_line_on_a_document_without_vat_is_an_error() -> None:
    receipt = no_vat_receipt(coded(TaxType.NONE, TaxType.STANDARD_5))

    issue = only(receipt, "line_vat_without_document_vat")

    assert (issue.severity, issue.field) == (Severity.ERROR, "lines[1].tax_type")


@pytest.mark.parametrize(
    "tax", [TaxType.NONE, TaxType.ZERO, TaxType.EXEMPT, TaxType.REVERSE_CHARGE]
)
def test_no_vat_codes_on_a_document_without_vat_are_fine(tax: TaxType) -> None:
    assert "line_vat_without_document_vat" not in codes(no_vat_receipt(coded(tax, tax)))


def test_zero_vat_counts_as_no_vat() -> None:
    receipt = invoice(vat=Decimal("0"), total=Decimal("1000.00"),
                      lines=coded(TaxType.STANDARD_5))  # fmt: skip

    assert "line_vat_without_document_vat" in codes(receipt)


def test_5_percent_lines_on_a_document_with_vat_are_fine() -> None:
    bill = invoice(lines=[line.model_copy(update={"tax_type": TaxType.STANDARD_5})
                          for line in invoice().lines])  # fmt: skip

    assert issues(bill) == []


def test_a_total_read_as_the_amount_before_vat_points_at_the_total() -> None:
    """Al Ain: 69.61 before VAT + VAT 3.48 = 73.09 printed; the model read 69.61 as the total."""
    lines = [Line(description="Milk", amount=Decimal("69.61"))]
    read = invoice(lines=lines, amount_type=AmountType.INCLUSIVE, vat=Decimal("3.48"),
                   total=Decimal("69.61"))  # fmt: skip

    issue = only(read, "total_before_vat", context("Sub total 69.61 VAT 3.48 Total 73.09"))

    assert (issue.severity, issue.field, issue.suggestion) == (Severity.ERROR, "total", "73.09")
    assert "vat_rate" not in codes(read)


def test_the_right_total_is_only_offered_when_printed() -> None:
    lines = [Line(description="Milk", amount=Decimal("69.61"))]
    read = invoice(lines=lines, amount_type=AmountType.INCLUSIVE, vat=Decimal("3.48"),
                   total=Decimal("69.61"))  # fmt: skip

    issue = only(read, "total_before_vat", context("Total 69.61"))

    assert issue.suggestion is None and "73.09" in issue.message
