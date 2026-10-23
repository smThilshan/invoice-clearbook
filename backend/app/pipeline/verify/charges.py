"""Charges outside the item lines (task 4.13, decision 36): delivery fees, service fees,
discounts, rounding... printed between the subtotal and the total.

The invoice schema has lines, subtotal, VAT and total; a receipt whose total also holds a
delivery fee fails "subtotal + VAT = total" although nothing was misread. When it does, the
page text is searched for printed amounts that explain the gap exactly:

1. The summary area: every labelled amount printed between the subtotal line and the total
   line, whatever its label says (so a charge never seen before is found too).
2. Otherwise: labelled amounts anywhere whose label holds a hint word (settings).

Only an exact explanation counts (sum within one fils) and only one: if two different sets of
charges would fit, nothing is offered. The accountant adds them as lines with one click (rule 8:
offered, never applied); when nothing explains the gap, the totals error stays.

Page text from a phone scanner often loses decimal points or whole amounts (EXP-004). So when
the stored text does not explain the gap and the page is a picture, our own OCR reads it once
(a few seconds, only for these documents) and the result is kept for later re-checks. An amount
printed without a decimal point ("141") is also tried as money ("1.41"); the exact sum decides.
"""

import re
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from decimal import Decimal
from itertools import combinations, product

from app.adapters.ocr import Image, OcrEngine
from app.adapters.storage import Storage
from app.adapters.storage.base import NotFoundError
from app.adapters.storage.keys import charge_ocr_key, page_image_key
from app.domain.invoice import Invoice
from app.models import Document
from app.pipeline.read import PageText, ocr_page
from app.pipeline.verify.layout import page_lines
from app.pipeline.verify.normalize import parse_amount

CENT = Decimal("0.01")
MAX_CANDIDATES = 12  # more labelled amounts than this in one area: not a receipt summary

# The last number on a printed line: its amount ("5.99", "1,050.10", "(5.00)", "-5.00").
_AMOUNT = re.compile(r"\(?-?\s*\d[\d,]*(?:\.\d+)?\)?-?\s*$")
# Words that make a line something other than a charge: totals, VAT (already counted),
# payment details. Matched as whole words, any case.
_NOT_A_CHARGE = re.compile(
    r"\b(sub\s*-?\s*total|total|vat|tax|cash|card|visa|master\s*card|paid|payment|change|"
    r"tendered|balance|amount\s+due|qty|quantity|items?)\b",
    re.IGNORECASE,
)
_NEGATIVE = re.compile(r"\b(discount|promo(?:tion)?|voucher|coupon|offer|saving|rebate)\b", re.I)
_SUBTOTAL = re.compile(r"\bsub\s*-?\s*total\b", re.IGNORECASE)
_TOTAL = re.compile(r"\btotal\b", re.IGNORECASE)
# A label needs a real word: a misread currency sign ("D", "AED", "Dhs") is not one.
_CURRENCY_TOKEN = re.compile(r"^(?:AED|DHS?|DIRHAMS?|[^\w\s]|\w)$", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class Charge:
    label: str  # as printed, e.g. "Delivery fee"
    amount: Decimal  # negative for a discount


@dataclass(frozen=True, slots=True)
class _Row:
    label: str
    amount: Decimal
    readings: tuple[Decimal, ...]  # the amount, and as money if its decimal point was lost


def total_gap(invoice: Invoice) -> Decimal | None:
    """What the total holds beyond the lines (or subtotal) and VAT; None when it adds up or
    cannot be told."""
    if invoice.total is None:
        return None
    base = invoice.subtotal
    if base is None:
        if not invoice.lines:
            return None
        base = sum((line.amount for line in invoice.lines), Decimal(0))
    gap = invoice.total - base - (invoice.vat or Decimal(0))
    return gap if abs(gap) > CENT else None


def find_charges(
    invoice: Invoice, texts: Sequence[list[PageText]], hint_words: Sequence[str]
) -> list[Charge] | None:
    """The printed charges that explain the gap exactly, or None. `texts`: readings of the
    pages, best first (the stored text, then our own OCR when one was made)."""
    gap = total_gap(invoice)
    if gap is None:
        return None
    hints = _hint_pattern(hint_words)
    for pages in texts:
        rows = [row for page in pages for line in page_lines(page) if (row := _row(line.text))]
        summary = _summary_area(rows, invoice)
        found = _explain(gap, summary) if summary else None
        if found is None and hints is not None:
            found = _explain(gap, [r for r in rows if hints.search(r.label)])
        if found is not None:
            return found
    return None


def ensure_charge_text(
    storage: Storage,
    document: Document,
    invoice: Invoice,
    pages: list[PageText],
    ocr: Callable[[], OcrEngine],
    hint_words: Sequence[str],
    decode: Callable[[bytes], Image],
) -> None:
    """Make (once) our own OCR of the pictures when the stored text cannot explain the gap.
    Only for documents whose total does not add up, and only for picture pages."""
    if total_gap(invoice) is None or find_charges(invoice, [pages], hint_words) is not None:
        return
    if load_charge_text(storage, document) or not any(p.source == "ocr" for p in pages):
        return
    engine = ocr()
    for page in pages:
        image = decode(storage.get(page_image_key(document.client_id, document.id, page.page)))
        text = ocr_page(engine, image, page.page)
        storage.put(charge_ocr_key(document.client_id, document.id, page.page), text.to_json())


def load_charge_text(storage: Storage, document: Document) -> list[PageText]:
    """Our own OCR made by `ensure_charge_text`; empty if none was needed."""
    return list(_charge_pages(storage, document))


def _charge_pages(storage: Storage, document: Document) -> Iterator[PageText]:
    for number in range(1, (document.page_count or 0) + 1):
        try:
            yield PageText.from_json(
                storage.get(charge_ocr_key(document.client_id, document.id, number))
            )
        except NotFoundError:
            return


def _summary_area(rows: list[_Row], invoice: Invoice) -> list[_Row]:
    """Charge rows between the subtotal line and the total line (first such pair)."""
    for start, row in enumerate(rows):
        if not (_SUBTOTAL.search(row.label) or _same(row.amount, invoice.subtotal)):
            continue
        for end in range(start + 1, len(rows)):
            if (
                _TOTAL.search(rows[end].label)
                and not _SUBTOTAL.search(rows[end].label)
                and (_same(rows[end].amount, invoice.total))
            ):
                between = rows[start + 1 : end]
                return [r for r in between if not _NOT_A_CHARGE.search(r.label)]
    return []


def _explain(gap: Decimal, candidates: list[_Row]) -> list[Charge] | None:
    """The one smallest set of candidates (with one reading each) adding up to the gap."""
    candidates = candidates[:MAX_CANDIDATES]
    for size in range(1, len(candidates) + 1):
        fits = [
            list(zip(subset, amounts, strict=True))
            for subset in combinations(candidates, size)
            for amounts in product(*(r.readings for r in subset))
            if abs(sum(amounts, Decimal(0)) - gap) <= CENT
        ]
        if len(fits) == 1:
            return [Charge(row.label, amount) for row, amount in fits[0]]
        if len(fits) > 1:
            return None  # two explanations: do not guess
    return None


def _row(text: str) -> _Row | None:
    """A printed line as label + amount ("Delivery fee D 5.99"), or None."""
    match = _AMOUNT.search(text.strip())
    if match is None:
        return None
    amount = parse_amount(match.group())
    if amount is None:
        return None
    tokens = text[: match.start()].replace(":", " ").split()
    while tokens and _CURRENCY_TOKEN.match(tokens[-1]):
        tokens.pop()  # "Delivery fee AED", "Delivery fee D" (a misread dirham sign)
    while tokens and _CURRENCY_TOKEN.match(tokens[0]):
        tokens.pop(0)
    label = " ".join(tokens).strip(" -" + chr(0x2013))  # spaces, hyphens, en dashes
    if not re.search(r"[^\W\d_]{3,}", label):
        return None  # no word: a bare number or a quantity
    if _NEGATIVE.search(label) and amount > 0:
        amount = -amount  # discounts are often printed without a minus
    readings: tuple[Decimal, ...] = (amount,)
    if "." not in match.group() and "," not in match.group() and abs(amount) >= 100:
        readings += (amount / 100,)  # "141" from a scanner that lost the point in "1.41"
    return _Row(label, amount, readings)


def _hint_pattern(words: Sequence[str]) -> re.Pattern[str] | None:
    if not words:
        return None
    return re.compile("|".join(rf"\b{re.escape(w)}\b" for w in words), re.IGNORECASE)


def _same(a: Decimal | None, b: Decimal | None) -> bool:
    return a is not None and b is not None and abs(abs(a) - abs(b)) <= CENT
