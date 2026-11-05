"""The line reconciler (decision 82): readings the page allows, tried together, kept only when
exactly one adds up to the proven total. Receipts as our OCR read them; amounts made up where
said."""

from decimal import Decimal
from typing import Any

from app.domain.enums import AmountType, DocumentType
from app.domain.invoice import Invoice, Line
from app.pipeline.read import PageText, TextSpan
from app.pipeline.reconcile_lines import reconcile_lines

D = Decimal

# Al Shahama 243: 2 x 7.50 = 15.00 twice (two different waters), 3.50, 9.50; total 43.00.
AL_SHAHAMA = """AL SHAHAMA SUPERMARKET
TRN: 100304657800003
MAI DUBAI LOW SODIUM WATER 1. 2.00 15.00
6297000611013 1 @ 7.50
MAI DUBAI LOW SODIUM WATER 50 2.00 15.00
6297000611044 1 @ 7.50
PURE ICE EDIBLE 1 KG 1.00 3.50
ALMARAI 100% APPLE NO ADDED S 1.00 9.50
Total Amount : 40.94
Total Tax : 2.06
Net Amount : 43.00
Vat % 5.00 40.94 2.06"""


def page(text: str) -> list[PageText]:
    rows = text.splitlines()
    spans = [TextSpan(row, (0.0, i / len(rows), 1.0, (i + 0.5) / len(rows)), None)
             for i, row in enumerate(rows)]  # fmt: skip
    return [PageText(1, 1000, 1400, "ocr", spans)]


def line(name: str, amount: str, qty: str | None = None, price: str | None = None) -> Line:
    return Line(description=name, amount=D(amount), qty=D(qty) if qty else None,
                unit_price=D(price) if price else None)  # fmt: skip


def receipt(*lines: Line, **fields: Any) -> Invoice:
    return Invoice(document_type=DocumentType.RECEIPT, lines=list(lines), **fields)


def amounts(invoice: Invoice) -> list[str]:
    return [str(x.amount) for x in invoice.lines]


WATER_1 = line("MAI DUBAI LOW SODIUM WATER 1.", "30.00", "2", "15.00")
WATER_50 = line("MAI DUBAI LOW SODIUM WATER 50", "30.00", "2", "15.00")
ICE = line("PURE ICE EDIBLE 1 KG", "3.50", "1", "3.50")
JUICE = line("ALMARAI 100% APPLE NO ADDED S", "9.50", "1", "9.50")


def test_a_list_read_twice_and_multiplied_again_is_put_right() -> None:
    read = receipt(WATER_1, WATER_50, ICE, JUICE, WATER_1, WATER_50, ICE, JUICE,
                   vat=D("2.06"), total=D("43.00"))  # fmt: skip

    fixed = reconcile_lines(read, page(AL_SHAHAMA))

    assert amounts(fixed) == ["15.00", "15.00", "3.50", "9.50"]
    assert [str(x.unit_price) for x in fixed.lines] == ["7.50", "7.50", "3.50", "9.50"]
    assert [x.description for x in fixed.lines][:2] == [WATER_1.description, WATER_50.description]
    assert fixed.amount_type is AmountType.INCLUSIVE


def test_lines_that_add_up_are_never_touched() -> None:
    """Three identical lines of a product bought three times: they add up, nothing changes."""
    masala = line("GARAM MASALA", "8.80", "1", "8.80")
    read = receipt(masala, masala, masala, vat=D("1.26"), total=D("26.40"))

    assert reconcile_lines(read, page("Garam masala 8.80\nVAT 1.26\nTotal 26.40")) is read


def test_without_the_total_printed_nothing_changes() -> None:
    """Two of the three would make 7.00, but 7.00 is printed nowhere: not proven."""
    read = receipt(ICE, ICE, ICE, vat=D("0.33"), total=D("7.00"))

    assert reconcile_lines(read, page("PURE ICE 3.50\nVAT 0.33\nNet Amount")) == read


def test_a_vat_not_five_percent_of_the_amount_before_it_proves_nothing() -> None:
    read = receipt(ICE, ICE, vat=D("0.50"), total=D("3.50"))

    assert reconcile_lines(read, page("PURE ICE 3.50\nVAT 0.50\nTotal 3.50")) == read


def test_the_total_row_copied_after_the_item_goes() -> None:
    """Air freshener: the model copied the total as a second line; the first one stays."""
    item, total_row = line("2 AIR FRESHENER", "18.00", "2", "9.00"), line("TOTAL", "18.00")
    read = receipt(item, total_row, total=D("18.00"))

    fixed = reconcile_lines(read, page("2 AIR FRESHENER 2 9.00 18.00\nTOTAL 18.00"))

    assert [x.description for x in fixed.lines] == ["2 AIR FRESHENER"]


def test_the_vat_is_never_kept_as_an_item_inside_the_total() -> None:
    """Porter: 40.19 + 2.01 = 42.20 would read the VAT as an item; 40.19 alone (VAT on top) and
    42.20 alone both fit too, so two answers: nothing changes."""
    read = receipt(line("Food", "40.19"), line("Food", "40.19"), line("VAT", "2.01"),
                   line("Total", "42.20"), vat=D("2.01"), total=D("42.20"))  # fmt: skip

    fixed = reconcile_lines(read, page("Food 40.19\nVAT 5% 2.01\nTotal 42.20"))

    assert fixed == read


def test_a_multiplied_amount_needs_its_price_printed_and_itself_printed_nowhere() -> None:
    """30.00 printed on the page: it may be right, so it is not read as multiplied."""
    read = receipt(WATER_1, ICE, vat=D("0.88"), total=D("18.50"))

    text = "WATER 2.00 15.00\nWATER 30.00\nICE 3.50\nVAT 0.88\nTotal 18.50"
    assert reconcile_lines(read, page(text)) == read


def test_a_price_not_exact_to_the_fils_once_divided_is_not_proven() -> None:
    odd = line("JUICE", "20.00", "3", "10.00")  # 10.00 / 3 = 3.333...
    read = receipt(odd, vat=D("0.48"), total=D("10.00"))

    assert reconcile_lines(read, page("JUICE 3.00 10.00\nVAT 0.48\nTotal 10.00")) == read


def test_two_different_answers_change_nothing() -> None:
    """Made up: from 4, 4, 1, 1, 3, 3, 2, 2 (20.00) to 15.00, removing a 4 and a 1 fits as
    well as a 3 and a 2: two answers, nothing changes."""
    a, b, c, d = line("A", "4.00"), line("B", "1.00"), line("C", "3.00"), line("D", "2.00")
    read = receipt(a, a, b, b, c, c, d, d, total=D("15.00"))

    assert reconcile_lines(read, page("A 4.00\nB 1.00\nC 3.00\nD 2.00\nTotal 15.00")) == read


def test_never_every_line_removed() -> None:
    read = receipt(line("Total", "10.00"), line("VAT", "0.48"), vat=D("0.48"), total=D("10.00"))

    fixed = reconcile_lines(read, page("Total 10.00\nVAT 0.48"))

    assert fixed.lines  # the total row may stay; at least one line always does
