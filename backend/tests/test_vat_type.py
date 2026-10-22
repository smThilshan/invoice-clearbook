"""VAT type from the amounts (decision 34): the amounts decide, never the model."""

from decimal import Decimal
from typing import Any

import pytest

from app.domain.enums import AmountType, DocumentType
from app.domain.invoice import Invoice, Line
from app.pipeline.verify.vat_type import vat_type_from_amounts


def bill(lines: list[str], vat: str | None, total: str | None, **changes: Any) -> Invoice:
    return Invoice(
        document_type=DocumentType.BILL,
        lines=[Line(description=f"Item {n}", amount=Decimal(a)) for n, a in enumerate(lines)],
        vat=Decimal(vat) if vat is not None else None,
        total=Decimal(total) if total is not None else None,
        **changes,
    )


@pytest.mark.parametrize(
    ("lines", "vat", "total", "expected"),
    [
        (["32.00", "8.00"], "2.00", "42.00", AmountType.EXCLUSIVE),  # VAT added on top
        (["33.60", "8.40"], "2.00", "42.00", AmountType.INCLUSIVE),  # lines already gross
        (["40.00"], None, "40.00", AmountType.NO_TAX),
        (["40.00"], "0.00", "40.00", AmountType.NO_TAX),
        (["32.00", "8.00"], "2.00", "42.01", AmountType.EXCLUSIVE),  # within one fils
        (["32.00", "8.00"], "2.00", "41.00", None),  # discount or misread: not guessed
        (["40.00"], None, "42.00", None),  # no VAT read but lines short of the total
    ],
)
def test_the_amounts_decide(
    lines: list[str], vat: str | None, total: str, expected: AmountType | None
) -> None:
    assert vat_type_from_amounts(bill(lines, vat, total)) is expected


def test_misleading_headings_do_not_matter() -> None:
    """EXP-004: the model said INCLUSIVE for a bill whose lines plus VAT made the total."""
    inv = bill(["32.00", "8.00", "7.00", "6.61"], "2.68", "56.29",
               amount_type=AmountType.INCLUSIVE)  # fmt: skip

    assert vat_type_from_amounts(inv) is AmountType.EXCLUSIVE


def test_without_lines_or_total_nothing_is_decided() -> None:
    assert vat_type_from_amounts(bill([], "2.00", "42.00")) is None
    assert vat_type_from_amounts(bill(["40.00"], "2.00", None)) is None
