from decimal import Decimal
from typing import Any

import pytest

from app.domain.enums import AmountType, DocumentType
from app.domain.invoice import Invoice, Line
from app.pipeline.vat_line import remove_vat_line


def receipt(lines: list[tuple[str, str]], **changes: Any) -> Invoice:
    fields: dict[str, Any] = {
        "document_type": DocumentType.BILL,
        "vat": Decimal("3.75"),
        "lines": [Line(description=d, amount=Decimal(a)) for d, a in lines],
    }
    fields.update(changes)
    return Invoice(**fields)


def summary(invoice: Invoice) -> tuple[list[str], str, str, str, AmountType | None]:
    return ([str(line.amount) for line in invoice.lines], str(invoice.subtotal),
            str(invoice.vat), str(invoice.total), invoice.amount_type)  # fmt: skip


def test_handwritten_receipt_vat_line_goes_and_the_evidence_total_is_taken() -> None:
    """Sunrise Bakery: the model put the grand total on the item, copied 'Vat @ 5%' as a line
    and left the total empty; its evidence says 78.75. 78.75 - 3.75 = 75.00, and 5% of it is
    3.75: VAT included in the item."""
    inv = receipt([("Cream Roll", "78.75"), ("Vat @ 5%", "3.75")], subtotal=Decimal("78.75"),
                  evidence={"total": "78.75"})  # fmt: skip

    assert summary(remove_vat_line(inv)) == (["78.75"], "75.00", "3.75", "78.75",
                                             AmountType.INCLUSIVE)  # fmt: skip


def test_vat_added_on_top_is_told_from_the_amounts() -> None:
    """Sparklean: 60 + 9 + VAT 3.45 = 72.45."""
    inv = receipt([("Surface san", "60.00"), ("Hand soap", "9.00"), ("VAT", "3.45")],
                  vat=Decimal("3.45"), total=Decimal("72.45"))  # fmt: skip

    assert summary(remove_vat_line(inv)) == (["60.00", "9.00"], "69.00", "3.45", "72.45",
                                             AmountType.EXCLUSIVE)  # fmt: skip


@pytest.mark.parametrize(
    ("lines", "changes"),
    [
        # no total and no evidence of one: nothing to tell the VAT type by
        ([("Cream Roll", "78.75"), ("Vat @ 5%", "3.75")], {}),
        # the line is not named VAT
        ([("Cream Roll", "75.00"), ("Delivery", "3.75")], {"total": Decimal("78.75")}),
        # an excise or tourism fee is not the VAT
        ([("Room", "75.00"), ("Tourism tax", "3.75")], {"total": Decimal("78.75")}),
        # the line is not the VAT amount read
        ([("Cream Roll", "75.00"), ("VAT", "3.70")], {"total": Decimal("78.75")}),
        # the rest does not fit the total
        ([("Cream Roll", "70.00"), ("VAT", "3.75")], {"total": Decimal("78.75")}),
        # the VAT is not 5% of the amount before VAT
        ([("Cream Roll", "60.00"), ("VAT", "3.75")], {"total": Decimal("63.75")}),
        # two VAT lines: which one?
        (
            [("Cream Roll", "75.00"), ("VAT", "3.75"), ("Vat 5%", "3.75")],
            {"total": Decimal("78.75")},
        ),
        # the VAT line is the only line
        ([("VAT", "3.75")], {"total": Decimal("3.75")}),
    ],
)
def test_nothing_changes_without_proof(lines: list[tuple[str, str]], changes: Any) -> None:
    inv = receipt(lines, **changes)

    assert remove_vat_line(inv) == inv


def test_evidence_holding_two_numbers_is_not_a_total() -> None:
    inv = receipt([("Cream Roll", "78.75"), ("Vat @ 5%", "3.75")],
                  evidence={"total": "75.00 + 3.75 = 78.75"})  # fmt: skip

    assert remove_vat_line(inv) == inv
