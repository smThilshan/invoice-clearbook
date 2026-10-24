"""Unit prices worked out from line totals (decision 44). All values are made up."""

import json
from decimal import Decimal
from typing import Any

import pytest

from app.domain.enums import AmountType, DocumentType
from app.domain.invoice import Invoice, Line
from app.pipeline.line_totals import repair_unit_prices, repaired_count

D = Decimal


def line(text: str, qty: str | None, unit: str | None, amount: str) -> Line:
    return Line(description=text, qty=D(qty) if qty else None,
                unit_price=D(unit) if unit else None, amount=D(amount))  # fmt: skip


def order(*lines: Line, subtotal: str | None = None, **fields: Any) -> Invoice:
    """An online order: no VAT, subtotal = the item lines (fees come after it)."""
    total = sum((x.amount for x in lines), D(0))
    return Invoice(document_type=DocumentType.RECEIPT, amount_type=AmountType.NO_TAX,
                   subtotal=D(subtotal) if subtotal else total, lines=list(lines),
                   **fields)  # fmt: skip


def units(invoice: Invoice) -> list[Decimal | None]:
    repaired, _ = repair_unit_prices(invoice)
    return [x.unit_price for x in repaired.lines]


def test_a_line_total_copied_into_the_unit_price_is_divided_when_proven() -> None:
    invoice = order(line("2 x Water 1.5L", "2", "7.98", "7.98"),
                    line("3 x Chicken breast 1kg", "3", "46.47", "46.47"),
                    line("Bread", "1", "4.00", "4.00"))  # fmt: skip

    repaired, changed = repair_unit_prices(invoice)

    assert [x.unit_price for x in repaired.lines] == [D("3.99"), D("15.49"), D("4.00")]
    assert [x.amount for x in repaired.lines] == [D("7.98"), D("46.47"), D("4.00")]  # untouched
    assert [x.qty for x in repaired.lines] == [D("2"), D("3"), D("1")]
    assert changed == 2


@pytest.mark.parametrize(
    ("lines", "why"),
    [
        ([line("3 x Juice", "3", "10.00", "10.00")], "3.333...: not exact to the fils"),
        ([line("1.235 x Cheese kg", "1.235", "20.00", "20.00")], "weighed: not exact"),
        ([line("3 x Water", "2", "7.98", "7.98")], "the text says 3, the qty field 2"),
        ([line("Water 1.5L", "2", "7.98", "7.98")], "no 'N x' to confirm the quantity"),
        ([line("2 x Water", "2", "3.99", "7.98")], "already a real unit price"),
        ([line("2 x Water", "2", "7.98", "3.99")], "swapped: unit price is not the amount"),
        ([line("2 x Free sample", "2", "0", "0")], "free item: unit price 0 is already right"),
    ],
)
def test_lines_that_are_not_proven_stay_as_read(lines: list[Line], why: str) -> None:
    invoice = order(*lines)

    assert repair_unit_prices(invoice) == (invoice, 0), why


def test_nothing_changes_when_the_lines_do_not_add_up_to_the_subtotal() -> None:
    # The printed subtotal includes a delivery fee: the amounts are not proven line totals.
    invoice = order(line("2 x Water", "2", "7.98", "7.98"), subtotal="14.98")

    assert units(invoice) == [D("7.98")]


def test_without_a_subtotal_the_total_proves_it_when_there_is_no_vat() -> None:
    lines = [line("2 x Water", "2", "7.98", "7.98")]
    invoice = Invoice(document_type=DocumentType.RECEIPT, total=D("7.98"), lines=lines)

    assert units(invoice) == [D("3.99")]


def test_with_vat_added_and_no_subtotal_nothing_is_proven() -> None:
    lines = [line("2 x Water", "2", "7.98", "7.98")]
    invoice = Invoice(document_type=DocumentType.BILL, amount_type=AmountType.EXCLUSIVE,
                      vat=D("0.40"), total=D("8.38"), lines=lines)  # fmt: skip

    assert units(invoice) == [D("7.98")]


def test_the_review_screen_count_compares_the_models_answer_with_what_was_kept() -> None:
    raw = json.dumps({"lines": [{"unit_price": 7.98}, {"unit_price": 4.0}]})
    data = {"lines": [{"unit_price": "3.99"}, {"unit_price": "4.00"}]}

    assert repaired_count(raw, data) == 1
    assert repaired_count("not json", data) == 0
