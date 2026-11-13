"""The table read twice (decision 95): the model lists every row, then lists them all again.

Life Smoothies prints an "Item" column (a short name: "Life Smoothies") beside the
"Description" column; the model read the three rows by their description, then the same three
by their item name, so the lines added up to twice the subtotal. Modern Bakery: four rows, eight
lines. The model's own numbers prove it (rule 7: nothing is guessed). The copy goes when all of
these hold (otherwise nothing changes and the checks flag the lines as before):
- The second half of the lines repeats the first, row by row: the same quantity, unit price
  and amount, in the same order; at least two rows (one repeated line is a total or payment
  row, `total_row.py`), none of them zero (a till's "Discount 0.00" and "Charity 0.00" rows).
- One half adds up to the subtotal or the total; both halves together do not. Two real rows
  that happen to be identical add up to the subtotal together, so they are kept.

The half with the fuller descriptions is kept, and the VAT type is set from the amounts, as
when the VAT line goes (decision 60). The model's own answer stays in `extractions.raw_output`,
and the checks say the copy was removed (`step._repeated_rows_removed`).
"""

from collections.abc import Sequence
from decimal import Decimal

from app.domain.invoice import Invoice, Line
from app.pipeline.verify.vat_type import vat_type_from_amounts

CENT = Decimal("0.01")
MIN_ROWS = 2


def remove_repeated_lines(invoice: Invoice) -> Invoice:
    kept = without_copy(invoice.lines, (invoice.subtotal, invoice.total))
    if kept is None:
        return invoice
    fixed = invoice.model_copy(update={"lines": kept})
    return fixed.model_copy(
        update={"amount_type": vat_type_from_amounts(fixed) or invoice.amount_type}
    )


def without_copy(lines: Sequence[Line], totals: Sequence[Decimal | None]) -> list[Line] | None:
    """The lines without their second copy, or None when they are not the table read twice.
    `totals`: the subtotal and the total, either may be missing."""
    half, odd = divmod(len(lines), 2)
    if odd or half < MIN_ROWS:
        return None
    first, second = lines[:half], lines[half:]
    pairs = zip(first, second, strict=True)
    if not all(_row(one) == _row(other) and one.amount > 0 for one, other in pairs):
        return None
    targets = [total for total in totals if total is not None]
    once = sum((line.amount for line in first), Decimal(0))
    if not _matches(once, targets) or _matches(once * 2, targets):
        return None
    return list(first if _words(first) >= _words(second) else second)


def _row(line: Line) -> tuple[Decimal | None, Decimal | None, Decimal]:
    return line.qty, line.unit_price, line.amount


def _matches(amount: Decimal, targets: Sequence[Decimal]) -> bool:
    return any(abs(amount - target) <= CENT for target in targets)


def _words(lines: Sequence[Line]) -> int:
    return sum(len(line.description or "") for line in lines)
