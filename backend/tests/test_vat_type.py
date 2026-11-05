"""VAT type from the amounts (decision 34): the amounts decide, never the model."""

from decimal import Decimal
from typing import Any

import pytest

from app.domain.enums import AmountType, DocumentType
from app.domain.invoice import Invoice, Line
from app.pipeline.verify.vat_type import likely_vat_type, vat_type_from_amounts


def bill(lines: list[str], vat: str | None, total: str | None, **changes: Any) -> Invoice:
    return Invoice(
        document_type=changes.pop("document_type", DocumentType.BILL),
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


# --- decision 84: a likely type when the amounts prove nothing -----------------------------

INCL, EXCL = AmountType.INCLUSIVE, AmountType.EXCLUSIVE


def likely(inv: Invoice, supplier: AmountType | None = None) -> AmountType | None:
    found = likely_vat_type(inv, supplier)
    return found.amount_type if found else None


def test_the_supplier_s_proven_type_comes_first() -> None:
    """Al Shahama 263: lines 33.75, total 33.25, VAT 1.58 (an unprinted 0.50 off)."""
    inv = bill(["5.25", "6.50", "22.00"], "1.58", "33.25")

    found = likely_vat_type(inv, INCL)

    assert found is not None and found.amount_type is INCL
    assert "supplier" in found.reason
    assert likely(inv, EXCL) is EXCL  # the supplier decides, not the nearest total


def test_the_total_the_lines_are_nearest_to_proves_nothing() -> None:
    """Amexhub: VAT 7.24 copied as an item and the discount 0.24 as a charge, 152.48, near the
    total 152.00, although the VAT is added on top. Lines that do not add up are misread."""
    assert likely(bill(["145.00", "0.24", "7.24"], "7.24", "152.00")) is None


def test_a_receipt_is_not_assumed_to_include_vat() -> None:
    """Porter: "Fare Without Tax 40.19, VAT 2.01, Total 42.20": a receipt with VAT on top."""
    receipt = bill(["40.19", "40.19"], "2.01", "42.20", document_type=DocumentType.RECEIPT)

    assert likely(receipt) is None


def test_no_vat_on_the_document_is_no_vat() -> None:
    assert likely(bill(["40.00"], None, "42.00")) is AmountType.NO_TAX


def test_amounts_that_prove_it_or_missing_amounts_are_left_to_the_amounts() -> None:
    assert likely(bill(["32.00", "8.00"], "2.00", "42.00"), INCL) is None  # proven: EXCLUSIVE
    assert likely(bill(["40.00"], "2.00", None), INCL) is None
    assert likely(bill([], "2.00", "42.00"), INCL) is None
