"""A till's barcode row read as an item (decision 90). Made-up amounts."""

from decimal import Decimal

import pytest

from app.domain.enums import AmountType, DocumentType
from app.domain.invoice import Invoice, Line
from app.pipeline.barcode_row import remove_barcode_rows

D = Decimal


def receipt(*lines: Line, total: str = "7.00") -> Invoice:
    return Invoice(document_type=DocumentType.RECEIPT, vat=D("0.33"), total=D(total),
                   lines=list(lines))  # fmt: skip


def item(text: str, qty: str, unit: str, amount: str) -> Line:
    return Line(description=text, qty=D(qty), unit_price=D(unit), amount=D(amount))


ICE = item("PURE ICE EDIBLE 1 KG", "2", "3.50", "7.00")


@pytest.mark.parametrize("row", ["6297000259048 1", "6291001002146 1 @ 3.50", "6297000259048"])
def test_the_barcode_row_goes_when_the_lines_then_add_up(row: str) -> None:
    """Al Shahama: 2 x 3.50 = 7.00, then "6297000259048 1 @ 3.50" read as a second item."""
    fixed = remove_barcode_rows(receipt(ICE, item(row, "1", "3.50", "3.50")))

    assert fixed.lines == [ICE]
    assert fixed.amount_type is AmountType.INCLUSIVE


@pytest.mark.parametrize(
    ("inv", "why"),
    [
        (
            receipt(ICE, item("6297000259048 1", "1", "3.50", "3.50"), total="10.50"),
            "the lines already add up: a second item",
        ),
        (
            receipt(ICE, item("6297000259048 1", "1", "3.50", "3.50"), total="9.00"),
            "without it the lines still do not add up",
        ),
        (
            receipt(ICE, item("6297000259048 1", "1", "2.00", "2.00"), total="7.00"),
            "not the price of the item above",
        ),
        (
            receipt(ICE, item("Water 6297000259048", "1", "3.50", "3.50"), total="7.00"),
            "a name, not only a barcode",
        ),
    ],
)
def test_anything_else_stays_as_read(inv: Invoice, why: str) -> None:
    assert remove_barcode_rows(inv) == inv, why
