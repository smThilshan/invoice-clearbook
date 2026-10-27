from decimal import Decimal
from typing import Any

from app.domain.enums import AmountType, DocumentType
from app.domain.invoice import Invoice, Line
from app.pipeline.printed_summary import apply_printed_summary
from app.pipeline.read import PageText, TextSpan

# UHM, as our OCR read it: the summary labels are garbled, the numbers are not.
UHM = """UHM UNITED HYPER MARKET
TRN:100228932800003
TAX INVOICE
B111# 7174123
1 6290400000135 3 5.99 17.97
JENAN PENNE REGATE
5033712115019 2 21.99 43.98
AL. KABEER BEEE CUBE
9903030008699 0.915 9.50 8.69
BEANS GREEN 10.67
CARROT LOOSE 3.99 7.94
ONION RED 3 5.50 16.50
DAILY FRESHT TTAITAN 330 15.99 5.28
GARLIC LOOSE 0.25
x* CASH 115.00
hount: 105.98
ISvOd 5.30
Bi ount: 111.28
can punt 115.00
Bal. An uL 3.72
VAT 5% 105.98 5.30"""

ITEMS = ["17.97", "43.98", "8.69", "10.67", "7.94", "16.50", "5.28", "0.25"]


def page(text: str) -> list[PageText]:
    rows = text.splitlines()
    spans = [TextSpan(row, (0.0, i / len(rows), 1.0, (i + 0.5) / len(rows)), None)
             for i, row in enumerate(rows)]  # fmt: skip
    return [PageText(1, 1000, 1400, "ocr", spans)]


def lines(*amounts: str) -> list[Line]:
    return [Line(description=f"line {n}", amount=Decimal(a)) for n, a in enumerate(amounts, 1)]


def receipt(*amounts: str, **fields: Any) -> Invoice:
    return Invoice(document_type=DocumentType.RECEIPT, lines=lines(*amounts), **fields)


def amounts(invoice: Invoice) -> list[str]:
    return [str(line.amount) for line in invoice.lines]


def summary(invoice: Invoice) -> tuple[str, str, str, AmountType | None]:
    return str(invoice.subtotal), str(invoice.vat), str(invoice.total), invoice.amount_type


def test_cash_and_vat_copied_as_lines_go_and_the_summary_is_taken_from_the_page() -> None:
    read = receipt(*ITEMS, "115.00", "5.30", subtotal=Decimal("115.00"), vat=Decimal("5.30"))

    fixed = apply_printed_summary(read, page(UHM))

    assert amounts(fixed) == ITEMS
    assert summary(fixed) == ("105.98", "5.30", "111.28", AmountType.INCLUSIVE)


def test_summary_rows_after_the_item_go_and_the_item_stays() -> None:
    """Al Shahama: the item costs the same as the "Net Amount" row after it."""
    text = ("CABLE LOCK RUBBER TOP 1.00 11.00\nTotal Amount 10.48\nTotal Tax 0.52\n"
            "Net Amount 11.00\nTendered Amount 11.00\nBalance Amount 0.00")  # fmt: skip
    read = receipt("11.00", "10.48", "0.52", "11.00", subtotal=Decimal("10.48"),
                   vat=Decimal("0.52"), total=Decimal("11.00"))  # fmt: skip

    fixed = apply_printed_summary(read, page(text))

    assert [line.description for line in fixed.lines] == ["line 1"]
    assert summary(fixed) == ("10.48", "0.52", "11.00", AmountType.INCLUSIVE)


def test_an_item_the_receipt_prints_twice_is_kept_once() -> None:
    text = "PURE ICE 1KG 2.00 7.00\n02 3.50\nTotal Amount 6.66\nTotal Tax 0.34\nNet Amount 7.00"
    read = receipt("7.00", "7.00", subtotal=Decimal("6.66"), vat=Decimal("0.34"),
                   total=Decimal("7.00"))  # fmt: skip

    assert amounts(apply_printed_summary(read, page(text))) == ["7.00"]


def test_a_missing_total_is_filled_when_the_lines_already_add_up_to_it() -> None:
    text = ("SUGAR 31.50\nJUICE 60.90\nVANILLA 18.90\nMILK 32.81\nTOTAL Before VAT 137.25\n"
            "6.86\n144.11")  # fmt: skip
    read = receipt("31.50", "60.90", "18.90", "32.81", subtotal=Decimal("137.25"),
                   vat=Decimal("6.86"))  # fmt: skip

    fixed = apply_printed_summary(read, page(text))

    assert len(fixed.lines) == 4
    assert summary(fixed) == ("137.25", "6.86", "144.11", AmountType.INCLUSIVE)


def test_prices_before_vat_are_kept_as_such() -> None:
    """Porter: the fare, then the fare without tax, the VAT and the total copied as lines."""
    text = "Trip Fare AED 21.65\nFare Without Tax AED21.65\nVAT AED1.08\nTotal Order Fare AED 22.73"
    read = receipt("21.65", "21.65", "1.08", "22.73", subtotal=Decimal("21.65"),
                   vat=Decimal("1.08"), total=Decimal("22.73"))  # fmt: skip

    fixed = apply_printed_summary(read, page(text))

    assert amounts(fixed) == ["21.65"]
    assert fixed.amount_type is AmountType.EXCLUSIVE


def test_a_document_that_adds_up_is_never_touched() -> None:
    right = receipt(*ITEMS, subtotal=Decimal("105.98"), vat=Decimal("5.30"),
                    total=Decimal("111.28"), amount_type=AmountType.INCLUSIVE)  # fmt: skip

    assert apply_printed_summary(right, page(UHM)) == right


def test_an_item_line_at_the_end_is_never_removed() -> None:
    """Noon: two invoices in one file; the second one's bread is not a summary row."""
    text = ("Delivery 4.76 5.00% 0.24 5.00\nCOD 2.86 5.00% 0.14 3.00\nSubtotal 7.62 AED\n"
            "0.38 AED\nTotal 8.00\nBread 18.50\nWhite Bread 4.95")  # fmt: skip
    read = receipt("5.00", "3.00", "18.50", "4.95", subtotal=Decimal("7.62"),
                   vat=Decimal("0.38"), total=Decimal("8.00"))  # fmt: skip

    assert apply_printed_summary(read, page(text)) == read


def test_a_summary_that_disagrees_with_the_total_read_changes_nothing() -> None:
    """Dream Night: items 3.99 = 3.80 + 0.19, but the total paid is 4.00 after rounding."""
    text = "YOGHURT 3.99 3.99\nTotal Before VAT: 3.80\nRoundoff: 0.01\nVAT: 0.19\nNetAmount 4.00"
    read = receipt("3.99", "0.01", "0.19", subtotal=Decimal("3.80"), vat=Decimal("0.19"),
                   total=Decimal("4.00"))  # fmt: skip

    assert apply_printed_summary(read, page(text)) == read


def test_two_possible_summaries_change_nothing() -> None:
    text = "PURE ICE 7.00\nTotal Amount 6.66\nTotal Tax 0.34\nNet Amount 7.00\n6.67 0.33"
    read = receipt("7.00", "7.00", total=Decimal("7.00"))

    assert apply_printed_summary(read, page(text)) == read


def test_no_printed_vat_summary_changes_nothing() -> None:
    """Talabat: no VAT on the screen (decision 52 handles its fee)."""
    text = "Subtotal D 98.87\nDelivery fee D 0.00\nService fee D 2.97\nTotal D 101.84"
    read = receipt("98.87", "2.97", "2.97", subtotal=Decimal("98.87"), vat=Decimal("2.97"),
                   total=Decimal("101.84"))  # fmt: skip

    assert apply_printed_summary(read, page(text)) == read


def test_a_credit_note_is_left_as_it_is() -> None:
    text = "CREDIT NOTE\nReturn 105.00\nNet 100.00\nVAT 5.00\nTotal 105.00"
    credit = Invoice(document_type=DocumentType.CREDIT_NOTE, lines=lines("-105.00", "-5.00"),
                     subtotal=Decimal("-100.00"), vat=Decimal("-5.00"),
                     total=Decimal("-105.00"))  # fmt: skip

    assert apply_printed_summary(credit, page(text)) == credit


def test_without_lines_nothing_changes() -> None:
    empty = receipt(total=Decimal("111.28"))

    assert apply_printed_summary(empty, page(UHM)) == empty
