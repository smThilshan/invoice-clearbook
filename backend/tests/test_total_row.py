"""A handwritten total copied as the last line (decision 90). Made-up amounts."""

from decimal import Decimal
from typing import Any

import pytest

from app.domain.enums import AmountType, DocumentType
from app.domain.invoice import Invoice, Line
from app.pipeline.total_row import remove_total_row

D = Decimal


def pad(*lines: tuple[str, str], **changes: Any) -> Invoice:
    items = [Line(description=d, amount=D(a)) for d, a in lines]
    fields: dict[str, Any] = {"document_type": DocumentType.RECEIPT, "vat": D(0), "lines": items}
    return Invoice(**{**fields, **changes})


def test_the_total_row_goes_and_becomes_the_total_from_the_evidence() -> None:
    """Vegetable pad: 28 + 16 + 56 = 100 written under the items; the model copied it as a line
    "Total" 100, left the total empty, and copied "100" as the total's evidence."""
    inv = pad(("Egg", "28"), ("Blueberry", "16"), ("Avocado", "56"), ("Total", "100"),
              evidence={"total": "100"})  # fmt: skip

    fixed = remove_total_row(inv)

    assert [x.description for x in fixed.lines] == ["Egg", "Blueberry", "Avocado"]
    assert (fixed.total, fixed.amount_type) == (D("100"), AmountType.NO_TAX)


def test_a_total_row_equal_to_the_total_read_goes_even_when_the_lines_are_misread() -> None:
    """7 read as 2: the lines make 54, the total 59. The total row still goes; the lines stay
    for the accountant."""
    fixed = remove_total_row(pad(("Avocado", "38"), ("Capsicum", "16"), ("TOTAL", "59"),
                                 total=D("59")))  # fmt: skip

    assert [x.amount for x in fixed.lines] == [D("38"), D("16")]
    assert (fixed.total, fixed.amount_type) == (D("59"), None)


@pytest.mark.parametrize(
    ("inv", "why"),
    [
        (pad(("Egg", "28"), ("Total", "20"), evidence={"total": "28"}), "not the total"),
        (pad(("Egg", "28"), ("Total", "28")), "no total, no evidence"),
        (pad(("Total", "28"), ("Egg", "28"), total=D("28")), "not the last line"),
        (pad(("Egg", "28"), ("Total Qty", "28"), total=D("28")), "not only a total"),
        (pad(("Total", "28"), total=D("28")), "the only line"),
    ],
)
def test_anything_else_stays_as_read(inv: Invoice, why: str) -> None:
    assert remove_total_row(inv) == inv, why
