"""Clean line descriptions after extraction (decision 42): what the accountant would delete by
hand, removed only when the document itself proves it is not part of the description.

- "5 x Sugar 1kg" -> "Sugar 1kg" only when 5 is the line's quantity: the qty field holds it.
- "105 FULL CREAM MILK" -> "FULL CREAM MILK" only when every line of the document (2 or more)
  starts with such a code: a code column, as some suppliers print. A size ("500 ml") is never a
  code. One line starting with a number is left alone.

The model's own answer stays in `extractions.raw_output`; the prompt is unchanged (EXP-004: any
wording change shifted other fields).
"""

import json
import re
from decimal import Decimal
from typing import Any

from app.domain.invoice import Invoice, Line

QTY_PREFIX = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*[xX\u00d7]\s+(?=\S)")  # x, X or the sign
CODE_PREFIX = re.compile(r"^\s*(\d{3,})\s+(?=[^\W\d_])")
# fmt: off
UNITS = frozenset({
    "ml", "g", "gm", "gms", "gram", "grams", "kg", "kgs", "l", "lt", "ltr", "ltrs", "litre",
    "litres", "liter", "liters", "pc", "pcs", "piece", "pieces", "x", "pack", "packs", "pkt",
    "ctn", "carton", "cartons", "box", "boxes",
})
# fmt: on
MIN_LINES_FOR_CODES = 2


def clean_descriptions(invoice: Invoice) -> tuple[Invoice, int]:
    """(invoice with cleaned descriptions, how many lines changed)."""
    texts = [_without_quantity(line) for line in invoice.lines]
    if _code_column(texts):
        texts = [CODE_PREFIX.sub("", text, count=1) if text else text for text in texts]
    changed = sum(text != line.description for text, line in zip(texts, invoice.lines, strict=True))
    if not changed:
        return invoice, 0
    lines = [
        line.model_copy(update={"description": text})
        for line, text in zip(invoice.lines, texts, strict=True)
    ]
    return invoice.model_copy(update={"lines": lines}), changed


def _without_quantity(line: Line) -> str | None:
    text = line.description
    match = QTY_PREFIX.match(text) if text else None
    if text is None or match is None or line.qty is None:
        return text
    if Decimal(match.group(1)) != line.qty:
        return text  # the numbers disagree: something may be misread, so leave it to be seen
    return text[match.end() :]


def _code_column(texts: list[str | None]) -> bool:
    if len(texts) < MIN_LINES_FOR_CODES:
        return False
    for text in texts:
        match = CODE_PREFIX.match(text) if text else None
        if match is None:
            return False
        next_word = text[match.end() :].split(maxsplit=1)[0].lower()  # type: ignore[index]
        if next_word.rstrip(".,") in UNITS:
            return False  # "500 ml ...": a size, not a code
    return True


def cleaned_count(raw_output: str, data: dict[str, Any] | None) -> int:
    """How many line descriptions the stored extraction has cleaned, compared with the model's
    own answer (shown on the review screen, so the cleanup is never hidden)."""
    try:
        answer = json.loads(raw_output)
    except ValueError:
        return 0
    if not isinstance(answer, dict) or data is None:
        return 0
    read = [line.get("description") for line in answer.get("lines") or [] if isinstance(line, dict)]
    kept = [line.get("description") for line in data.get("lines") or []]
    if len(read) != len(kept):
        return 0  # not the same lines: nothing comparable
    return sum(before != after for before, after in zip(read, kept, strict=True))
