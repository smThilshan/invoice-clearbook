"""The document date from every clue (decision 85).

The model's date alone was trusted: missed when the page says "Receipt Date" (Blue Tower
Facilities), read the wrong way round when the shop prints month first ("03/12/26", GALA:
3 December, in the future, for 12 March). Here every clue is weighed, deterministic Python:

- the model's date and the text it quoted for it (`evidence`);
- each date printed on the page, with the label before it: issue labels ("Date", "Receipt
  Date", "Tax Date", ...) name the document date; others ("Due", "Printed", "Delivery", ...)
  never do;
- limits a document date must keep: not in the future, not after the photo was taken (a date
  in the file name, "IMG-20250815-WA0017"), within the folder's month and not long before it;
- the supplier's way of writing dates (day or month first), learned from its own documents.

The folder only ever chooses between readings the page allows; it never replaces a printed
date. A date is changed only when exactly one reading fits; otherwise nothing changes and the
checks flag it as before. Every change is shown with its reason (`DateDecision`).
"""

import calendar
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from enum import StrEnum
from pathlib import PurePosixPath

from app.pipeline.read import PageText
from app.pipeline.verify.layout import page_lines
from app.pipeline.verify.normalize import DateReading, date_readings

PHOTO_SLACK = timedelta(days=1)  # a photo's name may carry the next day (time zones)
LABEL_WORDS = 3  # a date's label: the words just before it ("Bill No :263 Date")

_ISSUE_LABEL = re.compile(
    r"\b(?:date|dated|dt)\b|تاريخ|التاريخ",
    re.IGNORECASE,
)
_OTHER_LABEL = re.compile(
    r"\b(?:due|delivery|delivered|deliver|expiry|expires|exp|order|period|printed|print|"
    r"valid|validity|from|to|until|till|since|birth|dob|lpo|po|start|end|issued\s+on\s+card)\b",
    re.IGNORECASE,
)
_PHOTO_NAME = re.compile(r"(?<!\d)(20\d{2})[-_.]?(\d{2})[-_.]?(\d{2})(?!\d)")
_NUMERIC_DATE = re.compile(r"(?<!\d)(\d{1,2})[-/.](\d{1,2})[-/.](?:\d{4}|\d{2})(?!\d)")


class DateOrder(StrEnum):
    DAY_FIRST = "day_first"
    MONTH_FIRST = "month_first"


@dataclass(frozen=True, slots=True)
class DateLimits:
    """What a document date must keep (`fits`)."""

    today: date
    future_slack_days: int
    folder: date | None = None  # the folder's date (decision 25); None = not known
    max_days_before_folder: int = 0
    photo: date | None = None  # the date in the file name; None = none
    folder_year_known: bool = True  # False: "August/14th August", its year was assumed

    def fits(self, value: date) -> bool:
        if value > self.today + timedelta(days=self.future_slack_days):
            return False
        if self.photo is not None and value > self.photo + PHOTO_SLACK:
            return False
        folders = folder_years(self.folder, known=self.folder_year_known)
        return not folders or any(self._in_folder(value, folder) for folder in folders)

    def _in_folder(self, value: date, folder: date) -> bool:
        month_end = calendar.monthrange(folder.year, folder.month)[1]
        if value > folder.replace(day=month_end):  # "March 11" holds 12 March too
            return False
        return value >= folder - timedelta(days=self.max_days_before_folder)


def folder_years(folder: date | None, *, known: bool) -> list[date]:
    """The dates a folder may stand for: its own; also a year earlier when its name has no
    year (decision 31 assumed the latest, but last August's receipts are filed under
    "August" too)."""
    if folder is None:
        return []
    if known:
        return [folder]
    day = min(folder.day, calendar.monthrange(folder.year - 1, folder.month)[1])
    return [folder, folder.replace(year=folder.year - 1, day=day)]


def folder_for(document_date: date | None, folder: date | None, *, known: bool) -> date | None:
    """The folder date to check a document date against: of the years it may stand for, the
    one nearest the document's."""
    folders = folder_years(folder, known=known)
    if not folders or document_date is None:
        return folder
    return min(folders, key=lambda candidate: abs((candidate - document_date).days))


@dataclass(frozen=True, slots=True)
class DateDecision:
    value: date
    reason: str  # why, for the accountant: "read from 'Receipt Date' on the page"
    alternative: date | None = None  # the other reading, offered with one click


def resolve_date(
    model_date: date | None,
    model_text: str | None,
    pages: Sequence[PageText],
    limits: DateLimits,
    order: DateOrder | None = None,
) -> DateDecision | None:
    """A better document date than the model's, with its reason; None = the model's stands.
    `model_text` is what the model quoted for the date (its evidence)."""
    reading = _model_reading(model_date, model_text)
    if reading is not None:
        if reading.ambiguous:
            return _either_way(reading, limits, order)
        if limits.fits(reading.value):
            return None
    return _from_page(pages, limits, order, besides=reading.value if reading else None)


def photo_date(source_path: str, today: date) -> date | None:
    """The date in a photo's or scan's file name ("IMG-20250815-WA0017.jpg", "Scan -
    2026-02-27 11_48_07.pdf"); None when there is none or it cannot be one."""
    match = _PHOTO_NAME.search(PurePosixPath(source_path).name)
    if match is None:
        return None
    try:
        value = date(*(int(part) for part in match.groups()))
    except ValueError:
        return None
    return value if value <= today + PHOTO_SLACK else None


def date_order(texts: Iterable[str | None]) -> DateOrder | None:
    """How a supplier writes dates, from the date texts of its documents: proven only by dates
    that read one way alone (25/03/26 is day first, 03/25/26 month first), all agreeing."""
    seen: set[DateOrder] = set()
    for text in texts:
        match = _NUMERIC_DATE.search(text or "")
        if match is None:
            continue
        first, second = int(match[1]), int(match[2])
        if first > 12 >= second:
            seen.add(DateOrder.DAY_FIRST)
        elif second > 12 >= first:
            seen.add(DateOrder.MONTH_FIRST)
    return seen.pop() if len(seen) == 1 else None


def _model_reading(model_date: date | None, model_text: str | None) -> DateReading | None:
    """The model's date as it read it: from its quoted text when that holds its date (so a
    date that reads two ways is known), else the date alone."""
    readings = date_readings(model_text or "")
    for _, reading in readings:
        if model_date in (reading.value, reading.alternative):
            return reading
    return DateReading(model_date, ambiguous=False) if model_date else None


def _either_way(
    reading: DateReading, limits: DateLimits, order: DateOrder | None
) -> DateDecision | None:
    """03/12/26: 3 December (day first, as the model's date) or 12 March. Changed only to the
    month-first reading, and only when it is the one that fits."""
    month_first = reading.alternative
    if month_first is None:
        return None
    fit = [value for value in (reading.value, month_first) if limits.fits(value)]
    if len(fit) == 2 and order is not None:
        fit = [reading.value if order is DateOrder.DAY_FIRST else month_first]
        why = "this supplier writes the month first"
    else:
        why = "only that reading fits the folder, today and the photo"
    if fit != [month_first]:
        return None
    return DateDecision(month_first, f"read month first ({why})", reading.value)


def _from_page(
    pages: Sequence[PageText], limits: DateLimits, order: DateOrder | None, besides: date | None
) -> DateDecision | None:
    """The date printed after an issue label, else the only date on the page, when exactly one
    fits. Dates after other labels (Due, Printed ...) never count."""
    labelled: dict[date, str] = {}
    plain: set[date] = set()
    for label, reading in _page_dates(pages):
        values = _readings_that_fit(reading, limits, order) - {besides}
        if _OTHER_LABEL.search(label):
            continue
        if _ISSUE_LABEL.search(label):
            labelled |= {value: label for value in values if value not in labelled}
        else:
            plain |= values
    if len(labelled) == 1:
        [(value, label)] = labelled.items()
        return DateDecision(value, f"read from '{_shown(label)}' on the page")
    if not labelled and len(plain) == 1:
        return DateDecision(plain.pop(), "the only date printed on the page")
    return None


def _page_dates(pages: Sequence[PageText]) -> list[tuple[str, DateReading]]:
    """(label, reading) for each date on the pages; the label is the text before the date on
    its line, or the line above when the date stands alone ("Date\\n05/04/2026")."""
    found = []
    for page in pages:
        lines = [line.text for line in page_lines(page)]
        for number, text in enumerate(lines):
            for start, reading in date_readings(text):
                label = text[:start].strip(" :.-")
                if not label and number > 0:
                    label = lines[number - 1]
                found.append((" ".join(label.split()[-LABEL_WORDS:]), reading))
    return found


def _readings_that_fit(
    reading: DateReading, limits: DateLimits, order: DateOrder | None
) -> set[date]:
    values = {reading.value} | ({reading.alternative} if reading.alternative else set())
    fit = {value for value in values if limits.fits(value)}
    if len(fit) == 2 and order is not None:
        chosen = reading.value if order is DateOrder.DAY_FIRST else reading.alternative
        return {chosen or reading.value}
    return fit


def _shown(label: str) -> str:
    """The label's words as printed, without the number before them: "Receipt Date",
    "Date" (not "No: INV-00291 Date")."""
    words: list[str] = []
    for word in reversed(label.split()):
        bare = word.strip(":.-#")
        if not bare.isalpha():
            break
        words.insert(0, bare)
    return " ".join(words) or label
