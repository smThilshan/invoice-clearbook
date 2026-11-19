"""Decision 96: a total the model left empty though it quoted it. CRS/17933's own numbers."""

from decimal import Decimal
from typing import Any

import pytest

from app.domain.enums import AmountType
from app.domain.enums import DocumentType as T
from app.domain.invoice import Invoice, Line
from app.pipeline.missing_total import fill_total_from_evidence


def line(description: str, amount: str) -> Line:
    return Line(description=description, amount=Decimal(amount))


def crs(**changes: Any) -> Invoice:
    fields: dict[str, Any] = {"document_type": T.BILL, "total": None, "subtotal": Decimal("115.00"),
                              "vat": Decimal("5.75"), "amount_type": AmountType.INCLUSIVE,
                              "lines": [line("AVOCADO HASS", "66.00"), line("ORANGE", "49.00")],
                              "evidence": {"total": "120.75"}}  # fmt: skip
    fields.update(changes)
    return Invoice(**fields)


def test_the_quoted_total_its_amounts_prove_is_taken() -> None:
    fixed = fill_total_from_evidence(crs())

    assert fixed.total == Decimal("120.75")
    assert fixed.amount_type is AmountType.EXCLUSIVE  # 115.00 + 5.75 = 120.75, from the amounts


def test_a_no_vat_slip_gets_its_total_too() -> None:
    slip = crs(vat=Decimal("0"), subtotal=Decimal("115.00"), evidence={"total": "AED 115.00"})

    fixed = fill_total_from_evidence(slip)

    assert (fixed.total, fixed.amount_type) == (Decimal("115.00"), AmountType.NO_TAX)


@pytest.mark.parametrize(
    "changes",
    [
        {"evidence": {"total": "120.75 incl. VAT 5.75"}},  # two amounts: which one?
        {"evidence": {"total": "Total"}},  # no amount
        {"evidence": {}},  # nothing quoted
        {"evidence": {"total": "121.75"}},  # not the subtotal plus the VAT
        {"lines": [line("AVOCADO HASS", "66.00")]},  # lines short of the subtotal
        {"lines": []},
        {"subtotal": None},
        {"vat": None},
    ],
)
def test_nothing_changes_unless_the_amounts_prove_it(changes: dict[str, Any]) -> None:
    unproven = crs(**changes)

    assert fill_total_from_evidence(unproven) == unproven


def test_a_total_the_model_gave_is_never_replaced() -> None:
    given = crs(total=Decimal("120.70"))

    assert fill_total_from_evidence(given) == given
