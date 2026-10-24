"""Unit prices from line totals (decision 44): online orders print "2 x Water 1.5L  7.98", one
price per line, the line's total. The model then copies 7.98 into the unit price too. The unit
price is worked out in code, only when the document itself proves it, and never invented:

- the quantity is not 1, and the unit price read equals the line amount;
- "N x" at the start of the description says the same quantity (two readings agree);
- the line amounts add up to the printed subtotal (or total): they are line totals;
- amount / quantity is exact to the fils.

Otherwise the line stays as read and the line check keeps warning. Only the unit price changes,
never an amount: the money that reaches the books is what was printed. The model's own answer
stays in `extractions.raw_output`; settings.repair_unit_prices turns this off.
"""

import json
from decimal import Decimal, InvalidOperation
from typing import Any

from app.domain.invoice import Invoice, Line
from app.pipeline.descriptions import QTY_PREFIX
from app.pipeline.verify.rules import CENT, lines_target


def repair_unit_prices(invoice: Invoice) -> tuple[Invoice, int]:
    """(invoice with unit prices worked out, how many lines changed)."""
    target, _ = lines_target(invoice)
    if target is None or not invoice.lines:
        return invoice, 0
    if abs(sum((line.amount for line in invoice.lines), Decimal(0)) - target) > CENT:
        return invoice, 0  # the amounts are not proven to be line totals
    lines = [_repaired(line) for line in invoice.lines]
    changed = sum(new is not old for new, old in zip(lines, invoice.lines, strict=True))
    if not changed:
        return invoice, 0
    return invoice.model_copy(update={"lines": lines}), changed


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
