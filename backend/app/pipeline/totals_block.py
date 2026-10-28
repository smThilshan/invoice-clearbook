"""The printed totals block (decision 64): receipts print, under the items, labelled rows such
as "Total Amount : 17.50 / Total Tax : 0.87 / Rounding : -0.12 / Net Amount : 18.25 /
Tendered 50.00 / Balance 31.75". The model often swaps the total and the amount before VAT,
copies these rows as items, or copies an item twice. Right after extraction, Python reads the
block and, when it proves the answer, the totals and the items are taken from it.

Labels name candidates only; the arithmetic decides, because words differ between tills
("Total Amount" is before VAT on one, "Sub Total" includes VAT on another):
- A total T from a row labelled as a total, a VAT V > 0 from a row labelled VAT or tax, and the
  printed rounding r (0 when none) with 5% of (T - r - V) = V (within per-line rounding).
- Exactly one such answer (T, V, r); when the slack fits two (small amounts), the one whose
  amount before VAT is printed too.

Then the items: lines at the end that are printed in the block (copied summary rows, the cash
given, the change, an item copied again) are removed, as few as needed, never every line, until
the rest add up exactly to T - r (VAT included in the prices) or to T - r - V (VAT on top). One
item left a fils away from T - r is the till's own rounding of that line ("18.38" with
"17.50 + 0.87"): the VAT summary is what the VAT is claimed from, so the item takes its amount.
A printed rounding becomes the line "Rounding" with no VAT, as in decision 58; the subtotal is
T - V, and the VAT type is set from the amounts. Otherwise nothing changes, and the steps after
(decisions 56-61) run as before. The model's own answer stays in `extractions.raw_output`.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from app.domain.enums import DocumentType, TaxType
from app.domain.invoice import Invoice, Line
from app.pipeline.read import PageText
from app.pipeline.verify.layout import page_lines
from app.pipeline.verify.normalize import fold_ocr_digits
from app.pipeline.verify.vat_type import vat_type_from_amounts

CENT = Decimal("0.01")
UAE_VAT = Decimal("0.05")
RATE_SLACK = Decimal("0.02")  # VAT worked out per line rounds a little differently
MAX_ROUNDING = Decimal("0.24")  # cash is rounded to the nearest 0.25 at most
ROUNDING = "Rounding"

# A number on a printed line ("18.25", "-0.12", ".55", "1,050.00", "14.852"); not a rate ("5%")
# and not digits inside a code ("IDXB0690").
_NUMBER = re.compile(r"(?<![\w.,])-?(?:\d[\d,]*(?:\.\d+)?|\.\d+)(?![\w%.,])")
_ROUNDING = re.compile(r"round", re.IGNORECASE)
_VAT = re.compile(r"\b(?:vat|tax)\b", re.IGNORECASE)
# "incl" may come out "inc1" (OCR digit folding reads l as 1 next to a dot)
_NOT_VAT = re.compile(r"inc[l1i]|exc[l1i]|taxable|before|\btrn\b|\breg", re.IGNORECASE)
_TOTAL = re.compile(r"total|net\s*amount|amount\s*due|\btot\b|grand|payable", re.IGNORECASE)
_NOT_TOTAL = re.compile(r"\bqty\b|quantity|items?\b|\bunits?\b", re.IGNORECASE)
_PAID = re.compile(r"tender|\bcash\b|\bcard\b|\bpaid\b|visa|master", re.IGNORECASE)
_CHANGE = re.compile(r"balance|\bchange\b", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class TotalsBlock:
    total: Decimal
    vat: Decimal
    rounding: Decimal  # 0 when none is printed
    printed: frozenset[Decimal]  # every amount printed from the block down, unsigned
    paid: frozenset[Decimal] = frozenset()  # labelled as money given (Tendered, Cash, Card)
    change: frozenset[Decimal] = frozenset()  # labelled as change given back (Balance)

    @property
    def items(self) -> Decimal:
        """What the items add up to with VAT included: the total before the cash rounding."""
        return self.total - self.rounding

    @property
    def before_vat(self) -> Decimal:
        return self.total - self.rounding - self.vat


def read_totals_block(pages: Sequence[PageText]) -> TotalsBlock | None:
    rows: list[tuple[str, Decimal]] = []  # (label, amount) from the first total row down
    printed: set[Decimal] = set()
    for page in pages:
        for pairs in _labelled_lines(page):
            if not rows and not any(_is_total(label) for label, _ in pairs):
                continue  # still above the block: items and header
            rows += pairs
            printed.update(abs(amount) for _, amount in pairs)
    if not rows:
        return None
    totals = {amount for label, amount in rows if _is_total(label) and amount > 0}
    vats = {amount for label, amount in rows if _is_vat(label) and amount > 0}
    roundings = {amount for label, amount in rows if _ROUNDING.search(label)}
    if len(roundings) > 1 or any(abs(r) > MAX_ROUNDING for r in roundings):
        return None
    rounding = roundings.pop() if roundings else Decimal(0)
    answers = {
        (total, vat)
        for total in totals
        for vat in vats
        if vat < total - rounding - vat
        and abs((total - rounding - vat) * UAE_VAT - vat) <= RATE_SLACK
    }
    if len(answers) > 1:  # small amounts: the slack fits more than one; printed decides
        answers = {(t, v) for t, v in answers if abs(t - rounding - v) in printed}
    if len(answers) != 1:
        return None  # nothing proven, or two answers: never guess
    total, vat = answers.pop()
    paid = frozenset(amount for label, amount in rows if _PAID.search(label) and amount > 0)
    change = frozenset(amount for label, amount in rows if _CHANGE.search(label) and amount >= 0)
    return TotalsBlock(total, vat, rounding, frozenset(printed), paid, change)


def apply_totals_block(invoice: Invoice, pages: Sequence[PageText]) -> Invoice:
    if not invoice.lines or invoice.document_type in (DocumentType.CREDIT_NOTE,
                                                      DocumentType.NON_POSTABLE):  # fmt: skip
        return invoice
    block = read_totals_block(pages)
    if block is None:
        return invoice
    items = _items(invoice.lines, block)
    if items is None:
        return invoice
    if block.rounding:
        items = [*items, Line(description=ROUNDING, amount=block.rounding, tax_type=TaxType.NONE)]
    fixed = invoice.model_copy(update={
        "lines": items, "subtotal": block.total - block.vat, "vat": block.vat,
        "total": block.total,
    })  # fmt: skip
    return fixed.model_copy(update={"amount_type": vat_type_from_amounts(fixed)})


def _items(lines: list[Line], block: TotalsBlock) -> list[Line] | None:
    """The item lines once copied block rows at the end are removed, fewest first; None if
    no number of them leaves items that add up to the block."""
    targets = (block.items, block.before_vat)
    # The printed VAT copied as a line named VAT (decision 60): with it, the lines would also
    # add up to the total as if VAT were included, so it goes first.
    vat_rows = [n for n, line in enumerate(lines) if line.amount == block.vat
                and _is_vat(line.description or "")]  # fmt: skip
    if len(vat_rows) == 1 and len(lines) > 1:
        lines = lines[: vat_rows[0]] + lines[vat_rows[0] + 1 :]
    for removed in range(len(lines)):  # never every line
        if removed and not _block_row(lines[-removed].amount, block):
            break  # a real item: nothing before it is a copied row either
        rest = lines[: len(lines) - removed]
        if sum((line.amount for line in rest), Decimal(0)) in targets:
            return rest
    # One item left, a fils from the VAT summary: the till rounded that line on its own.
    item = lines[0]
    copied_rows = all(_block_row(line.amount, block) for line in lines[1:])
    if copied_rows and abs(item.amount - block.items) == CENT and item.qty in (None, Decimal(1)):
        price = block.items if item.unit_price is not None else None
        return [item.model_copy(update={"amount": block.items, "unit_price": price})]
    return None


def _block_row(amount: Decimal, block: TotalsBlock) -> bool:
    """A line the model copied from the block: one of its amounts, the items' sum, or 0.00."""
    return amount == 0 or abs(amount) in block.printed or amount == block.items


def _labelled_lines(page: PageText) -> list[list[tuple[str, Decimal]]]:
    """(label, amount) pairs per printed line. On a tilted photo a label can land just above
    its amount ("Total Amount:" then "5.24"): a line holding only words gives them to the
    first unlabelled amount of the next line, when it is within one character height and to
    the right of the words."""
    result: list[list[tuple[str, Decimal]]] = []
    words: tuple[str, float, float, float] | None = None  # text, centre, height, right edge
    for line in page_lines(page):
        pairs = _pairs(fold_ocr_digits(line.text) if line.ocr else line.text)
        if words and pairs and not pairs[0][0]:
            text, y, height, right = words
            first = next((s for s in line.spans if _NUMBER.search(s.text)), None)
            if first and abs(line.y - y) <= height and first.box[0] >= right:
                pairs[0] = (text, pairs[0][1])
        result.append(pairs)
        words = None
        if not pairs and re.search(r"[A-Za-z]{2}", line.text):
            box = line.spans[-1].box
            words = (line.text.strip(" :|-"), line.y, box[3] - box[1], box[2])
    return result


def _pairs(text: str) -> list[tuple[str, Decimal]]:
    """Each number on a line with the words printed before it ("Total Tax : 0.87")."""
    pairs, start = [], 0
    for match in _NUMBER.finditer(text):
        try:
            amount = Decimal(match.group().replace(",", ""))
        except InvalidOperation:
            continue
        pairs.append((text[start : match.start()].strip(" :|-"), amount))
        start = match.end()
    return pairs


def _is_total(label: str) -> bool:
    return bool(_TOTAL.search(label)) and not _NOT_TOTAL.search(label) and not _is_vat(label)


def _is_vat(label: str) -> bool:
    return bool(_VAT.search(label)) and not _NOT_VAT.search(label)
