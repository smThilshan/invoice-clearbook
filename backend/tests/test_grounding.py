from datetime import date
from decimal import Decimal
from typing import Any, Literal

import pytest

from app.domain.enums import DocumentType
from app.domain.invoice import Invoice
from app.pipeline.read import PageText, TextSpan
from app.pipeline.verify.grounding import check_grounding
from app.pipeline.verify.issues import Issue, Severity

PRINTED = """TAX INVOICE
Al Waha Hypermarket LLC   TRN 100-9990-0000-0001
Invoice No: INV-00291      Date: 15 Oct 2026      Due: 14/11/2026
Sub Total   1,000.00
VAT 5%         50.00
Total AED   1,050.00"""


def page(text: str = PRINTED, source: Literal["text_layer", "ocr"] = "text_layer") -> PageText:
    spans = [TextSpan(line, (0, 0, 1, 1), None) for line in text.splitlines()]
    return PageText(1, 1000, 1400, source, spans)


def invoice(**changes: Any) -> Invoice:
    fields: dict[str, Any] = {
        "document_type": DocumentType.BILL,
        "invoice_number": "INV-00291",
        "invoice_date": date(2026, 10, 15),
        "due_date": date(2026, 11, 14),
        "vendor_trn": "100999000000001",
        "subtotal": Decimal("1000.00"),
        "vat": Decimal("50.00"),
        "total": Decimal("1050.00"),
    }
    fields.update(changes)
    return Invoice(**fields)


def issues_for(inv: Invoice, *pages: PageText) -> list[Issue]:
    return check_grounding(inv, pages or (page(),))


def fields(inv: Invoice, *pages: PageText) -> set[str | None]:
    return {issue.field for issue in issues_for(inv, *pages)}


def test_values_printed_on_the_document_are_grounded() -> None:
    assert issues_for(invoice()) == []


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("total", Decimal("1060.00")),
        ("subtotal", Decimal("1010.00")),
        ("vat", Decimal("55.00")),
        ("invoice_date", date(2026, 10, 16)),
        ("invoice_number", "INV-00292"),
        ("vendor_trn", "100999000000002"),
    ],
)
def test_value_not_on_document_is_an_error(field: str, value: object) -> None:
    [issue] = issues_for(invoice(**{field: value}))

    assert (issue.code, issue.severity, issue.field) == ("not_on_document", Severity.ERROR, field)
    assert str(value) in issue.message


def test_due_date_not_printed_is_only_a_warning() -> None:
    [issue] = issues_for(invoice(due_date=date(2026, 11, 15)))

    assert (issue.field, issue.severity) == ("due_date", Severity.WARNING)


def test_number_matches_despite_spaces_symbols_and_case() -> None:
    printed = page(PRINTED.replace("INV-00291", "inv 00291"))

    assert "invoice_number" not in fields(invoice(invoice_number="INV-00291"), printed)


def test_trn_matches_with_or_without_separators() -> None:
    assert "vendor_trn" not in fields(invoice(vendor_trn="100 999 000 000 001"))


def test_negative_amounts_on_credit_notes_match_printed_values() -> None:
    credit = invoice(document_type=DocumentType.CREDIT_NOTE, total=Decimal("-1050.00"),
                     subtotal=Decimal("-1000.00"), vat=Decimal("-50.00"))  # fmt: skip

    assert issues_for(credit) == []


def test_zero_vat_need_not_be_printed() -> None:
    printed = page("Invoice No: INV-00291 Date: 15 Oct 2026 Due: 14/11/2026 "
                   "TRN 100999000000001 Total 1,000.00")  # fmt: skip

    assert issues_for(invoice(vat=Decimal("0"), subtotal=None, total=Decimal("1000.00")),
                      printed) == []  # fmt: skip


def test_ambiguous_printed_date_accepts_either_reading() -> None:
    printed = page(PRINTED.replace("15 Oct 2026", "02/09/2026"))

    assert "invoice_date" not in fields(invoice(invoice_date=date(2026, 9, 2)), printed)
    assert "invoice_date" not in fields(invoice(invoice_date=date(2026, 2, 9)), printed)


# --- OCR tolerance -------------------------------------------------------------------------

OCR_MISREAD = PRINTED.replace("1,050.00", "1,O5O.OO").replace("INV-00291", "INV-OO29l")


def test_ocr_digit_confusions_are_tolerated_on_ocr_pages() -> None:
    assert issues_for(invoice(), page(OCR_MISREAD, source="ocr")) == []


def test_text_layer_gets_no_ocr_tolerance() -> None:
    found = fields(invoice(), page(OCR_MISREAD, source="text_layer"))

    assert found == {"total", "invoice_number"}


def test_ocr_tolerance_does_not_invent_unrelated_values() -> None:
    assert "total" in fields(invoice(total=Decimal("9999.00")), page(OCR_MISREAD, source="ocr"))


def test_values_on_any_page_count() -> None:
    first = page("TAX INVOICE  Invoice No: INV-00291  Date: 15 Oct 2026  TRN 100999000000001")
    last = PageText(2, 1000, 1400, "text_layer",
                    [TextSpan("Sub Total 1,000.00 VAT 50.00 Total 1,050.00 Due 14/11/2026",
                              (0, 0, 1, 1), None)])  # fmt: skip

    assert issues_for(invoice(), first, last) == []


# --- missed numbers (decision 17) ---------------------------------------------------------


@pytest.mark.parametrize(
    "label", ["Invoice No: 4512", "Receipt No. 88", "INV # 12", "Tax Invoice Number 7",
              "رقم الفاتورة 12"],
)  # fmt: skip
def test_missing_number_with_label_on_page_is_likely_missed(label: str) -> None:
    [issue] = issues_for(invoice(invoice_number=None), page(PRINTED.replace(
        "Invoice No: INV-00291", label)))  # fmt: skip

    assert (issue.code, issue.severity) == ("number_likely_missed", Severity.WARNING)


def test_missing_number_without_label_is_not_flagged_here() -> None:
    receipt_text = page("AL WAHA HYPERMARKET 15 Oct 2026 Due 14/11/2026 TRN 100999000000001 "
                        "Sub Total 1,000.00 VAT 50.00 TOTAL 1,050.00")  # fmt: skip

    assert issues_for(invoice(invoice_number=None), receipt_text) == []


def test_non_postable_documents_are_not_grounded() -> None:
    quote = Invoice(document_type=DocumentType.NON_POSTABLE, total=Decimal("1.00"))

    assert check_grounding(quote, [page()]) == []


def test_human_verified_fields_are_not_grounded() -> None:
    typed = invoice(total=Decimal("1060.00"), invoice_number="INV-00292")

    assert fields(typed) == {"total", "invoice_number"}
    assert check_grounding(typed, [page()], verified={"total", "invoice_number"}) == []
    assert {i.field for i in check_grounding(typed, [page()], verified={"total"})} == {
        "invoice_number"
    }
