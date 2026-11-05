"""The line reconciler (decision 82): when the lines do not add up to a total the page proves,
the readings the page allows are tried together and the one that adds up is kept. The steps
before it (decisions 56, 60, 64, 66) each fix one kind of slip; the model often makes two at
once (Al Shahama 243: the list read twice, and each water line multiplied again by its
quantity, 146.00 for a 43.00 receipt), and then none of them can prove anything.

Proven total: printed on the page; with VAT, the VAT printed too and 5% of the total less it.
The lines must then add up to the total (VAT included) or the total less the VAT (on top).

Readings, per group of identical lines (same description, quantity, price and amount), so a
product bought twice and the same line read twice are told apart by the arithmetic only:
- read twice: keep fewer of them, at least one;
- a summary row read as a line (its amount is the total, the VAT, the amount before VAT or
  the printed VAT rate 5.00): may also go entirely;
- multiplied: the quantity is not 1, the amount is quantity x price and is printed nowhere,
  the price is printed: the price was the line's amount (2.00 x 15.00 read as 30.00), so the
  amount becomes the price and the price amount / quantity, exact to the fils.

The VAT is never kept as an item inside the total (decision 60): a line of the VAT's amount
stays only beside lines that make the amount before VAT.

Kept: the answer with the fewest changed lines, only when it is the only one with that few
(or they keep the same amounts and differ only in which of two equal lines stays: the earlier
one, as a receipt prints its summary after the items); never every line removed. Otherwise
nothing changes and the checks flag it as before. No supplier names, no label words. The
model's own answer stays in `extractions.raw_output`.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from itertools import product

from app.domain.invoice import Invoice, Line
from app.pipeline.read import PageText
from app.pipeline.verify.normalize import find_amounts, fold_ocr_digits, to_ascii_digits
from app.pipeline.verify.vat_type import vat_type_from_amounts

CENT = Decimal("0.01")
UAE_VAT = Decimal("0.05")
RATE_SLACK = Decimal("0.02")  # VAT worked out per line rounds a little differently
MAX_ANSWERS = 5000  # readings tried at most; a longer list is left to the accountant
VAT_RATE = Decimal(5)


@dataclass(frozen=True, slots=True)
class _Group:
    line: Line
    positions: tuple[int, ...]  # where the identical lines were read; the first are kept
    keep: tuple[int, ...]  # how many of them may be kept
    multiplied: Line | None  # the line read as multiplied, corrected; None if not possible


_Answer = list[tuple[int, Line]]  # (position read, line), the kept lines


def reconcile_lines(invoice: Invoice, pages: Sequence[PageText]) -> Invoice:
    total, vat = invoice.total, invoice.vat or Decimal(0)
    if not invoice.lines or total is None or _adds_up(invoice.lines, total, vat):
        return invoice
    printed = _printed(pages)
    if abs(total) not in printed or (vat and not _vat_proven(total, vat, printed)):
        return invoice
    targets = {total, total - vat} if vat else {total}
    summary = {total, vat, total - vat} - {Decimal(0)}
    if vat and VAT_RATE in printed:
        summary.add(VAT_RATE)  # "Vat % 5.00" from the receipt's VAT table
    groups = _groups(invoice.lines, summary, printed)
    choices = [[(k, m) for k in g.keep for m in ((False, True) if g.multiplied else (False,))]
               for g in groups]  # fmt: skip
    tries = 1
    for options in choices:
        tries *= len(options)
    if tries > MAX_ANSWERS:
        return invoice

    best: list[_Answer] = []
    fewest = len(invoice.lines) + 1
    for answer in product(*choices):
        kept: _Answer = []
        changes = 0
        for group, (count, multiplied) in zip(groups, answer, strict=True):
            line = group.multiplied if multiplied and group.multiplied else group.line
            kept += [(position, line) for position in group.positions[:count]]
            changes += len(group.positions) - count + (count if multiplied else 0)
        if not kept or changes == 0 or changes > fewest:
            continue
        amount = sum((line.amount for _, line in kept), Decimal(0))
        fits = targets
        if vat and any(line.amount == vat for _, line in kept):
            fits = {total - vat}  # the VAT kept as an item inside the total: never (dec. 60)
        if any(abs(amount - target) <= CENT for target in fits):
            if changes < fewest:
                fewest, best = changes, []
            best.append(kept)
    chosen = _one(best)
    if chosen is None:
        return invoice  # nothing fits, or two different answers: never guess
    fixed = invoice.model_copy(update={"lines": [line for _, line in sorted(chosen)]})
    return fixed.model_copy(update={"amount_type": vat_type_from_amounts(fixed)})


def _one(answers: list[_Answer]) -> _Answer | None:
    """The only answer; or, when every answer keeps the same amounts and differs only in which
    of two equal lines stays (an item and the total row copied after it), the one keeping the
    earlier lines: a receipt prints its summary after the items (as decision 56)."""
    if not answers:
        return None
    amounts = {tuple(sorted(line.amount for _, line in a)) for a in answers}
    if len(answers) > 1 and len(amounts) > 1:
        return None
    return min(answers, key=lambda a: sorted(position for position, _ in a))


def _adds_up(lines: Sequence[Line], total: Decimal, vat: Decimal) -> bool:
    amount = sum((line.amount for line in lines), Decimal(0))
    return abs(amount - total) <= CENT or abs(amount + vat - total) <= CENT


def _vat_proven(total: Decimal, vat: Decimal, printed: set[Decimal]) -> bool:
    return abs(vat) in printed and abs((total - vat) * UAE_VAT - vat) <= RATE_SLACK


def _printed(pages: Sequence[PageText]) -> set[Decimal]:
    exact = "\n".join(page.text for page in pages)
    ocr = "\n".join(fold_ocr_digits(page.text) for page in pages if page.source == "ocr")
    return find_amounts(to_ascii_digits(f"{exact}\n{ocr}"))


def _groups(lines: Sequence[Line], summary: set[Decimal], printed: set[Decimal]) -> list[_Group]:
    positions: dict[str, list[int]] = {}
    for position, line in enumerate(lines):
        positions.setdefault(line.model_dump_json(), []).append(position)  # identical lines
    groups = []
    for where in positions.values():
        line = lines[where[0]]
        lowest = 0 if line.amount in summary else 1
        groups.append(_Group(line, tuple(where), tuple(range(lowest, len(where) + 1)),
                             _multiplied(line, printed)))  # fmt: skip
    return groups


def _multiplied(line: Line, printed: set[Decimal]) -> Line | None:
    """2.00 x 15.00 = 30.00, 30.00 printed nowhere and 15.00 printed: the line's amount is
    15.00 and its price 7.50. None unless every part of that holds."""
    qty, price, amount = line.qty, line.unit_price, line.amount
    if qty is None or price is None or qty <= 0 or qty == 1:
        return None
    if abs(qty * price - amount) > CENT or abs(amount) in printed or abs(price) not in printed:
        return None
    unit = price / qty
    if unit != unit.quantize(CENT):
        return None  # not exact to the fils: not proven
    return line.model_copy(update={"amount": price, "unit_price": unit.quantize(CENT)})
