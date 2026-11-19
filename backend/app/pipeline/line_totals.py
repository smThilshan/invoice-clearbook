"""Unit prices from line totals (decision 44): online orders print "2 x Water 1.5L  7.98", one
price per line, the line's total. The model then copies 7.98 into the unit price too. The unit
price is worked out in code, only when the document itself proves it, and never invented:

- the quantity is not 1, and the unit price read equals the line amount;
- "N x" at the start of the description says the same quantity (two readings agree);
- the line amounts add up to the printed subtotal (or total): they are line totals. The model's
  own subtotal is that witness here: this runs right after extraction, before it is replaced
  by the worked-out one (decision 81), and only an exact match proves anything;
- amount / quantity is exact to the fils.

Otherwise the line stays as read and the line check keeps warning, unless the line totals
alone are proven (decision 90): a handwritten pad has an empty Rate column, and the model
copies each amount into it ("500g" capsicum, 2.00 x 2.00 = 2.00). With the lines adding up to
the total, a unit price equal to its amount while the quantity is not 1 cannot be right, and
nothing on the page says what it is: it is cleared (null over invented).

Only the unit price changes, never an amount: the money that reaches the books is what was
printed. The model's own answer stays in `extractions.raw_output`; settings.repair_unit_prices
turns the first repair off.
"""

import json
from decimal import Decimal, InvalidOperation
from typing import Any

from app.domain.enums import AmountType
from app.domain.invoice import Invoice, Line
from app.pipeline.descriptions import QTY_PREFIX
from app.pipeline.verify.rules import CENT
from app.pipeline.verify.vat_type import vat_type_from_amounts


def repair_unit_prices(invoice: Invoice) -> tuple[Invoice, int]:
    """(invoice with unit prices worked out, how many lines changed)."""
    target = _printed_items(invoice)
    if target is None or not invoice.lines:
        return invoice, 0
    if abs(sum((line.amount for line in invoice.lines), Decimal(0)) - target) > CENT:
        return invoice, 0  # the amounts are not proven to be line totals
    lines = [_repaired(line) for line in invoice.lines]
    changed = sum(new is not old for new, old in zip(lines, invoice.lines, strict=True))
    if not changed:
        return invoice, 0
    return invoice.model_copy(update={"lines": lines}), changed


def clear_copied_unit_prices(invoice: Invoice) -> Invoice:
    """Unit prices copied from the amount, cleared once the line totals add up (decision 90)."""
    if vat_type_from_amounts(invoice) is None:
        return invoice
    lines = [line.model_copy(update={"unit_price": None}) if _copied(line) else line
             for line in invoice.lines]  # fmt: skip
    if lines == invoice.lines:
        return invoice
    return invoice.model_copy(update={"lines": lines})


def _copied(line: Line) -> bool:
    qty = line.qty
    return (qty is not None and qty != 1 and line.unit_price == line.amount
            and qty * line.amount != line.amount)  # fmt: skip


def _printed_items(invoice: Invoice) -> Decimal | None:
    """What the line totals add up to: the total (VAT included, or no VAT), else the subtotal
    as read (an online order prints its items' sum before the fees)."""
    if invoice.amount_type is AmountType.INCLUSIVE:
        return invoice.total
    if invoice.subtotal is not None:
        return invoice.subtotal
    return invoice.total if not invoice.vat else None


def _repaired(line: Line) -> Line:
    qty, unit = line.qty, line.unit_price
    if qty is None or unit is None or qty <= 0 or qty == 1 or unit != line.amount:
        return line
    printed = QTY_PREFIX.match(line.description or "")
    if printed is None or Decimal(printed.group(1)) != qty:
        return line  # the quantity is not confirmed by the text: do not build on it
    each = (line.amount / qty).quantize(CENT)
    if each * qty != line.amount or each == unit:
        return line  # not exact to the fils: leave it to be seen (or nothing to change: 0.00)
    return line.model_copy(update={"unit_price": each})


def repaired_count(raw_output: str, data: dict[str, Any] | None) -> int:
    """How many unit prices the stored extraction worked out, compared with the model's own
    answer (shown on the review screen, so the repair is never hidden)."""
    try:
        answer = json.loads(raw_output)
    except ValueError:
        return 0
    if not isinstance(answer, dict) or data is None:
        return 0
    read = [line.get("unit_price") for line in answer.get("lines") or [] if isinstance(line, dict)]
    kept = [line.get("unit_price") for line in data.get("lines") or []]
    if len(read) != len(kept):
        return 0
    return sum(
        _decimal(before) != _decimal(after) for before, after in zip(read, kept, strict=True)
    )


def _decimal(value: object) -> Decimal | None:
    try:
        return Decimal(str(value)) if value is not None else None
    except InvalidOperation:
        return None
