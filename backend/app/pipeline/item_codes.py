"""Item codes (decision 82): the barcode a supermarket prints under each item ("6297000611013
1 @ 7.50"), read from the page text by our own code, never asked of the model. It tells the
same product from a different one when lines are sent item by item: two waters of the same
name and price but different sizes have different barcodes.

Only a GTIN whose check digit is right counts (EAN-13, UPC-A, EAN-8, GTIN-14), so a phone,
TRN or bill number is not taken for one by chance alone; OCR that runs the "1 @" on behind it
("629700061101319") is read for its first 13 digits. The code is taken from the item's own
printed row or the two rows under it, before the next item starts, in reading order. Nothing
found, or a row that could belong to two items: no code (null over invented).
"""

import re
from collections.abc import Sequence
from difflib import SequenceMatcher

from app.domain.invoice import Invoice, Line
from app.pipeline.read import PageText
from app.pipeline.verify.normalize import to_ascii_digits

_DIGITS = re.compile(r"(?<!\d)\d{8,16}(?!\d)")
_SAME_ITEM = 0.75  # how alike a printed row and the model's description must be
_ROWS_BELOW = 2  # the barcode is printed on the item's row or just under it


def attach_item_codes(invoice: Invoice, pages: Sequence[PageText]) -> Invoice:
    if not invoice.lines:
        return invoice
    rows = [to_ascii_digits(row) for page in pages for row in page.text.splitlines()]
    codes: list[str | None] = [line.item_code for line in invoice.lines]
    current: int | None = None  # the line whose rows are being read
    since = 0
    following = 0  # the next line expected in reading order
    for row in rows:
        found = _line_of(row, invoice.lines, following)
        if found is not None:
            current, since, following = found, 0, found + 1
        elif current is not None:
            since += 1
        if current is None or since > _ROWS_BELOW or codes[current] is not None:
            continue
        code = gtin(row)
        if code is not None:
            codes[current] = code
            current = None
    if codes == [line.item_code for line in invoice.lines]:
        return invoice
    lines = [line.model_copy(update={"item_code": code})
             for line, code in zip(invoice.lines, codes, strict=True)]  # fmt: skip
    return invoice.model_copy(update={"lines": lines})


def gtin(row: str) -> str | None:
    """The one valid GTIN on a printed row, or None (none, or more than one)."""
    found = set()
    for run in _DIGITS.findall(row):
        for length in (13, 14, 12, 8):
            exact = len(run) == length
            glued = length == 13 and 14 <= len(run) <= 15  # "1 @" run on behind it
            if (exact or glued) and _check_digit_ok(run[:length]):
                found.add(run[:length])
                break
    return found.pop() if len(found) == 1 else None


def _check_digit_ok(code: str) -> bool:
    """GS1 check digit: weights 3 and 1 from the right, the check digit excluded."""
    body, check = code[:-1], int(code[-1])
    total = sum(int(d) * (3 if n % 2 == 0 else 1) for n, d in enumerate(reversed(body)))
    return (10 - total % 10) % 10 == check


def _line_of(row: str, lines: Sequence[Line], following: int) -> int | None:
    """The line this row prints, in reading order: the next one, or the one after it (a line
    the model skipped does not stop the rest)."""
    text = _letters(row)
    if len(text) < 4:
        return None
    for index in range(following, min(following + 2, len(lines))):
        name = _letters(lines[index].description or "")
        if name and SequenceMatcher(None, text[: len(name) + 4], name).ratio() >= _SAME_ITEM:
            return index
    return None


def _letters(text: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", text.upper())
