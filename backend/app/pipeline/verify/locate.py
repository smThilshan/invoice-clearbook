"""Where a value is printed (task 4.6): the word boxes of every place a field's value appears,
for the Workbench to highlight. Same comparison as grounding (normalize.py): "1,050.00" printed
is 1050 typed, "4/5/2026" is 4 May, a TRN in groups or with OCR misreads (S for 5) is the TRN.

On each line, every smallest run of consecutive words that holds the value is a match; a run
that only adds words around a smaller match is dropped ("Total AED 1,050.00" -> "1,050.00").

Names are often tidied by the model ("Company L.L.C." for a printed "CO LLC") or misspelled by a
scanner: a text of several words with no exact match falls back to the lines holding most of its
words (one wrong letter allowed in longer words). Those matches are marked approximate.
"""

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum

from app.pipeline.read import PageText
from app.pipeline.verify.layout import Line, page_lines, union_box
from app.pipeline.verify.normalize import (
    find_amounts,
    find_dates,
    fold_ocr_digits,
    normalize_trn,
    parse_amount,
    to_ascii_digits,
)

MAX_WORDS = 8  # a value never spans more words than this on one line
MIN_TEXT = 2  # shorter text values would match everywhere
FUZZY_SHARE = 2 / 3  # approximate: at least this share of the value's words on one line
FUZZY_MIN_WORD = 3  # shorter words ("Al", "&") are ignored when matching approximately
ONE_TYPO_FROM = 5  # words this long may differ by one letter


class ValueKind(StrEnum):
    AMOUNT = "amount"
    DATE = "date"
    TRN = "trn"
    TEXT = "text"


@dataclass(frozen=True, slots=True)
class Match:
    page: int
    box: tuple[float, float, float, float]  # x0, y0, x1, y1 as fractions of the page
    exact: bool = True  # False: most words of a text found (see module docstring)


def locate(pages: Sequence[PageText], kind: ValueKind, value: str) -> list[Match]:
    """Every place `value` is printed; [] if it is nowhere, or is not a valid value of `kind`."""
    holds = _matcher(kind, value)
    if holds is None:
        return []
    matches: list[Match] = []
    for page in pages:
        for line in page_lines(page):
            for start, end in _smallest_runs(line, holds):
                match = Match(line.page, union_box(line.spans[start : end + 1]))
                if match not in matches:
                    matches.append(match)
    if not matches and kind is ValueKind.TEXT:
        return _approximate(pages, value)
    return matches


def _approximate(pages: Sequence[PageText], value: str) -> list[Match]:
    wanted = [word for word in map(_compact, value.split()) if len(word) >= FUZZY_MIN_WORD]
    if len(wanted) < 2:
        return []  # one word must match exactly
    matches = []
    for page in pages:
        for line in page_lines(page):
            words = [_compact(span.text) for span in line.spans]
            hits = {
                index
                for target in wanted
                for index, word in enumerate(words)
                if _same_word(word, target, ocr=line.ocr)
            }
            found = sum(any(_same_word(words[i], t, ocr=line.ocr) for i in hits) for t in wanted)
            if hits and found / len(wanted) >= FUZZY_SHARE:
                spans = line.spans[min(hits) : max(hits) + 1]
                matches.append(Match(line.page, union_box(spans), exact=False))
    return matches


def _same_word(word: str, target: str, *, ocr: bool) -> bool:
    if word == target:
        return True
    if ocr and _compact(fold_ocr_digits(word)) == _compact(fold_ocr_digits(target)):
        return True
    return len(target) >= ONE_TYPO_FROM and _one_edit_apart(word, target)


def _one_edit_apart(a: str, b: str) -> bool:
    """One letter changed, added or removed ("REGISTRALION" / "REGISTRATION")."""
    if abs(len(a) - len(b)) > 1 or a == b:
        return False
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b, strict=True)) == 1
    shorter, longer = sorted((a, b), key=len)
    return any(longer[:i] + longer[i + 1 :] == shorter for i in range(len(longer)))


Holds = Callable[[str, bool], bool]  # (text of some words, read by OCR?) -> holds the value?


def _matcher(kind: ValueKind, value: str) -> Holds | None:
    value = value.strip()
    if kind is ValueKind.AMOUNT:
        amount = parse_amount(value)
        if amount is None:
            return None
        wanted: Decimal = abs(amount)
        return lambda text, ocr: wanted in find_amounts(_digits_ready(text, ocr))
    if kind is ValueKind.DATE:
        try:
            day = date.fromisoformat(value)
        except ValueError:
            return None
        return lambda text, ocr: day in find_dates(_digits_ready(text, ocr))
    if kind is ValueKind.TRN:
        trn = normalize_trn(value)
        if len(trn) < MIN_TEXT:
            return None
        return lambda text, ocr: trn in re.sub(r"\D", "", _digits_ready(text, ocr))
    compact = _compact(value)
    if len(compact) < MIN_TEXT:
        return None
    return lambda text, ocr: (
        compact in _compact(text)
        or (ocr and _compact(fold_ocr_digits(value)) in _compact(fold_ocr_digits(text)))
    )


def _smallest_runs(line: Line, holds: Holds) -> list[tuple[int, int]]:
    """(first, last) word indexes of each run holding the value with nothing extra around it."""
    words = [span.text for span in line.spans]
    runs = []
    for start in range(len(words)):
        for end in range(start, min(len(words), start + MAX_WORDS)):
            if holds(" ".join(words[start : end + 1]), line.ocr):
                runs.append((start, end))
                break
    return [
        run for run in runs
        if not any(other != run and run[0] <= other[0] and other[1] <= run[1] for other in runs)
    ]  # fmt: skip


def _digits_ready(text: str, ocr: bool) -> str:
    text = to_ascii_digits(text)
    return fold_ocr_digits(text) if ocr else text


def _compact(text: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", to_ascii_digits(text).upper())
