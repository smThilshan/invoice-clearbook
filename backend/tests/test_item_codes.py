"""Item codes (decision 82): the printed barcode under each item, read by our own code. Rows as
our OCR read Al Shahama 243 ("1 @" runs on behind the barcode)."""

from decimal import Decimal

import pytest

from app.domain.enums import DocumentType
from app.domain.invoice import Invoice, Line
from app.pipeline.item_codes import attach_item_codes, gtin
from app.pipeline.read import PageText, TextSpan

ROWS = """AL SHAHAMA SUPERMARKET
TRN: 100304657800003
TEL:04-3252383
MAI DUBAI LOU SODIUM UATER 1.
629700061101319
MAI DUBAI LOW SODIUN WATER 50
62970006110441
PURE ICE EDIBLE 1 KG
3.50
Total Amount : 40.94"""


def page(text: str) -> list[PageText]:
    rows = text.splitlines()
    spans = [TextSpan(row, (0.0, i / len(rows), 1.0, (i + 0.5) / len(rows)), None)
             for i, row in enumerate(rows)]  # fmt: skip
    return [PageText(1, 1000, 1400, "ocr", spans)]


def receipt(*names: str) -> Invoice:
    lines = [Line(description=name, amount=Decimal("15.00")) for name in names]
    return Invoice(document_type=DocumentType.RECEIPT, lines=lines)


def codes(invoice: Invoice) -> list[str | None]:
    return [line.item_code for line in invoice.lines]


def test_each_item_gets_the_barcode_printed_under_it() -> None:
    read = receipt("MAI DUBAI LOW SODIUM UATER 1.", "MAI DUBAI LOW SODIUM WATER 50",
                   "PURE ICE EDIBLE 1 KG")  # fmt: skip

    assert codes(attach_item_codes(read, page(ROWS))) == ["6297000611013", "6297000611044", None]


@pytest.mark.parametrize(
    ("row", "code"),
    [
        ("6297000611013 1 @ 7.50", "6297000611013"),
        ("629700061101319", "6297000611013"),  # "1 @" read as digits behind it
        ("012345678905", "012345678905"),  # UPC-A
        ("96385074", "96385074"),  # EAN-8
        ("6297000611014", None),  # check digit wrong
        ("TRN: 100304657800003", None),
        ("6297000611013 6297000611044", None),  # two on one row: whose?
    ],
)
def test_only_a_barcode_whose_check_digit_is_right_counts(row: str, code: str | None) -> None:
    assert gtin(row) == code


def test_a_barcode_too_far_below_an_item_is_not_its_own() -> None:
    text = "PURE ICE EDIBLE 1 KG\n3.50\nVAT\nTotal\n6297000611013"

    assert codes(attach_item_codes(receipt("PURE ICE EDIBLE 1 KG"), page(text))) == [None]


def test_a_barcode_belongs_to_the_item_above_it_not_the_next() -> None:
    text = "PURE ICE EDIBLE 1 KG\nALMARAI APPLE JUICE\n6281007055413"
    read = receipt("PURE ICE EDIBLE 1 KG", "ALMARAI APPLE JUICE")

    assert codes(attach_item_codes(read, page(text))) == [None, "6281007055413"]
