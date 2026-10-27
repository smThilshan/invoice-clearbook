"""Normalizers: printed text -> comparable values (task 3.1). Pure functions, no model.

Used by grounding (does the extracted value appear on the page?), validation rules and the
duplicate business key. Conventions are UAE ones and are stated, never guessed silently:
- Amounts: comma = thousands, dot = decimal ("1,050.10"). A lone comma before 1-2 digits is a
  decimal comma ("50,10"); with both, the last one is the decimal mark ("1.050,10").
- Dates: day first ("02/09/2025" = 2 Sep). A numeric date that also reads month-first is
  marked ambiguous, and grounding accepts both readings.
- Digits: Arabic-Indic and Extended Arabic-Indic digits and separators are converted first.
"""

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

# Escapes, not literal characters: these look like ASCII punctuation in an editor.
_TO_ASCII = str.maketrans(
    {
        **{chr(0x0660 + n): str(n) for n in range(10)},  # Arabic-Indic digits
        **{chr(0x06F0 + n): str(n) for n in range(10)},  # Extended Arabic-Indic digits
        chr(0x066B): ".",  # Arabic decimal separator
        chr(0x066C): ",",  # Arabic thousands separator
        chr(0x2212): "-",  # minus sign
    }
)

_NUMBER = re.compile(r"\d[\d,.]*\d|\d")


# Letters OCR mistakes for digits ("1,O5O.1O", "100S"), only when they touch a digit or a number
# separator. Used on OCR text only (ours or a scanner's): an exact text layer gets no tolerance.
OCR_DIGITS = str.maketrans("OoQDlIiSsBZz", "000011155822")
_CONFUSABLE_NEAR_DIGIT = re.compile(r"(?<=[\d.,])[OoQDlIiSsBZz]|[OoQDlIiSsBZz](?=[\d.,])")


# A day misread before a month name ("Ol-Aug-2025", "I-Aug-25"): its letters touch no digit.
_DAY_BEFORE_MONTH = re.compile(
    r"(?<![A-Za-z0-9])[0-9OoQDlIi]{1,2}(?=[\-/.](?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov"
    r"|dec)[a-z]*[\-/.]\d)",
    re.IGNORECASE,
)


def fold_ocr_digits(text: str) -> str:
    """'1,O5O.1O' -> '1,050.10', 'Ol-Aug-2025' -> '01-Aug-2025'. Repeated, because each fix
    can expose a neighbour."""
    text = _DAY_BEFORE_MONTH.sub(lambda m: m.group().translate(OCR_DIGITS), text)
    previous = None
    while previous != text:
        previous = text
        text = _CONFUSABLE_NEAR_DIGIT.sub(lambda m: m.group().translate(OCR_DIGITS), text)
    return text


def to_ascii_digits(text: str) -> str:
    return text.translate(_TO_ASCII)


# --- amounts -----------------------------------------------------------------------------


def parse_amount(text: str) -> Decimal | None:
    """One printed amount ("AED 1,050.10", "(50.00)", "50.00-") -> Decimal. None if the text
    holds no number, several numbers, or an impossible grouping ("1,05,0")."""
    clean = to_ascii_digits(text).strip()
    tokens = _NUMBER.findall(clean)
    if len(tokens) != 1:
        return None
    value = _number_value(tokens[0])
    if value is None:
        return None
    negative = (
        (clean.startswith("(") and clean.endswith(")"))
        or re.search(r"-\s*\d", clean) is not None
        or re.search(r"\d\s*-$", clean) is not None
    )
    return -value if negative else value


def find_amounts(text: str) -> set[Decimal]:
    """Every number in a page's text, as an unsigned value (credit notes print minus signs
    in many ways). For grounding: is an extracted amount printed on the page?"""
    values = (_number_value(token) for token in _NUMBER.findall(to_ascii_digits(text)))
    return {value for value in values if value is not None}


def _number_value(token: str) -> Decimal | None:
    if "," in token and "." in token:
        decimal_mark = "." if token.rfind(".") > token.rfind(",") else ","
        thousands = "," if decimal_mark == "." else "."
        whole, fraction = token.rsplit(decimal_mark, 1)
        if thousands in fraction or not _grouped(whole, thousands):
            return None
        digits = f"{whole.replace(thousands, '')}.{fraction}"
    elif "," in token:
        parts = token.split(",")
        if len(parts) == 2 and len(parts[1]) in (1, 2):
            digits = f"{parts[0]}.{parts[1]}"  # decimal comma: "50,10"
        elif _grouped(token, ","):
            digits = token.replace(",", "")  # thousands: "1,050" = 1050
        else:
            return None
    elif "." in token:
        if token.count(".") == 1:
            digits = token  # "1.050" is one point zero five (dot = decimal)
        elif _grouped(token, "."):
            digits = token.replace(".", "")  # "1.050.000"
        else:
            return None
    else:
        digits = token
    try:
        return Decimal(digits)
    except InvalidOperation:
        return None


def _grouped(number: str, separator: str) -> bool:
    first, *rest = number.split(separator)
    return 1 <= len(first) <= 3 and all(len(group) == 3 for group in rest)


# --- dates -------------------------------------------------------------------------------

_MONTHS = {
    name: number
    for number, names in enumerate(
        (
            ("JAN", "JANUARY"),
            ("FEB", "FEBRUARY"),
            ("MAR", "MARCH"),
            ("APR", "APRIL"),
            ("MAY",),
            ("JUN", "JUNE"),
            ("JUL", "JULY"),
            ("AUG", "AUGUST"),
            ("SEP", "SEPT", "SEPTEMBER"),
            ("OCT", "OCTOBER"),
            ("NOV", "NOVEMBER"),
            ("DEC", "DECEMBER"),
        ),
        start=1,
    )
    for name in names
}
_MONTH = r"(?P<month_name>[A-Za-z]{3,9})\.?"
# The year, ending the date: a 4-digit year may run straight into a time when OCR loses the
# space ("25/03/202612:17", app screenshots); a 2-digit year never, it would be misread.
_YEAR = r"(?P<year>\d{4}(?=\d{1,2}:\d{2})|\d{4}(?!\d)|\d{2}(?!\d))"
_SEP = r"[\s\-/.,]*"
# (pattern, could day and month be the other way round?)
_DATE_PATTERNS: tuple[tuple[re.Pattern[str], bool], ...] = (
    # 2025-09-12, 2025/09/12: year first is always year-month-day
    (
        re.compile(r"(?<!\d)(?P<year>\d{4})[-/.](?P<month>\d{1,2})[-/.](?P<day>\d{1,2})(?!\d)"),
        False,
    ),
    # 12/09/2025, 12-09-25, 12.09.2025: read day first, but month first is also possible
    (
        re.compile(r"(?<!\d)(?P<day>\d{1,2})[-/.](?P<month>\d{1,2})[-/.]" + _YEAR),
        True,
    ),
    # 12 Sep 2025, 12-Sep-25, 12th September, 2025
    (
        re.compile(
            r"(?<!\d)(?P<day>\d{1,2})(?:st|nd|rd|th)?" + _SEP + _MONTH + _SEP + _YEAR,
            re.IGNORECASE,
        ),
        False,
    ),
    # Sep 12, 2025
    (
        re.compile(
            _MONTH + r"\s+(?P<day>\d{1,2})(?:st|nd|rd|th)?,?\s+(?P<year>\d{4})(?!\d)",
            re.IGNORECASE,
        ),
        False,
    ),
)


@dataclass(frozen=True, slots=True)
class DateReading:
    value: date  # day-first reading
    ambiguous: bool  # also a valid, different date read month-first (e.g. 02/09/2025)
    alternative: date | None = None


def parse_date(text: str) -> DateReading | None:
    """The first date in `text`, read day-first. None if there is none."""
    readings = _readings(to_ascii_digits(text))
    return readings[0] if readings else None


def find_dates(text: str) -> set[date]:
    """Every date in a page's text, both readings of ambiguous ones (for grounding)."""
    found: set[date] = set()
    for reading in _readings(to_ascii_digits(text)):
        found.add(reading.value)
        if reading.alternative is not None:
            found.add(reading.alternative)
    return found


def _readings(text: str) -> list[DateReading]:
    readings: list[tuple[int, DateReading]] = []
    for pattern, may_swap in _DATE_PATTERNS:
        for match in pattern.finditer(text):
            reading = _reading(match.groupdict(), may_swap=may_swap)
            if reading is not None:
                readings.append((match.start(), reading))
    readings.sort(key=lambda item: item[0])
    return [reading for _, reading in readings]


def _reading(parts: dict[str, str | None], *, may_swap: bool) -> DateReading | None:
    year = _full_year(parts["year"] or "")
    day = int(parts["day"] or 0)
    if parts.get("month_name"):
        month = _MONTHS.get((parts["month_name"] or "").upper())
        if month is None:
            return None
        value = _safe_date(year, month, day)
        return DateReading(value, ambiguous=False) if value else None

    month = int(parts["month"] or 0)
    value = _safe_date(year, month, day)
    if value is None:
        return None
    swapped = _safe_date(year, day, month) if may_swap and day != month else None
    return DateReading(value, ambiguous=swapped is not None, alternative=swapped)


def _full_year(year: str) -> int:
    return 2000 + int(year) if len(year) == 2 else int(year)


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


# --- identifiers -------------------------------------------------------------------------

# Labels that are printed before the number itself; never part of the identity.
_NUMBER_PREFIX = re.compile(
    r"^(?:TAX\s*INVOICE|INVOICE|INV|BILL|RECEIPT|RCPT|CREDIT\s*NOTE|CN|DOC|DOCUMENT)?"
    r"\s*(?:NO|NUMBER|NUM|NBR|REF)?\s*[.:#]*\s*",
    re.IGNORECASE,
)


def normalize_invoice_number(value: str) -> str | None:
    """Business-key form (architecture §10): "INV-00291", "inv 291", "#291" -> "291".
    Uppercase, labels and symbols removed, leading zeros of a numeric start dropped."""
    text = _NUMBER_PREFIX.sub("", to_ascii_digits(value).strip())
    compact = re.sub(r"[^A-Z0-9]", "", text.upper())
    if compact.isdigit() or (compact and compact[0].isdigit()):
        compact = compact.lstrip("0") or "0"
    return compact or None


def normalize_trn(value: str) -> str:
    """Digits only (spaces, dashes and Arabic-Indic digits handled). Validity is separate."""
    return re.sub(r"\D", "", to_ascii_digits(value))


def is_valid_trn(digits: str) -> bool:
    """UAE TRN: exactly 15 digits."""
    return len(digits) == 15 and digits.isdigit()


# --- currencies ----------------------------------------------------------------------------

# Code points, not literals: several of these look alike in an editor.
DIRHAM_SIGN = chr(0x20C3)  # UAE DIRHAM SIGN, Unicode 18.0 (CBUAE, March 2025): D with two bars
SAUDI_RIYAL_SIGN = chr(0x20C1)
OMANI_RIAL_SIGN = chr(0x20C4)

_CERTAIN_SYMBOLS = {
    DIRHAM_SIGN: "AED",
    SAUDI_RIYAL_SIGN: "SAR",
    OMANI_RIAL_SIGN: "OMR",
    chr(0x20AC): "EUR",  # euro sign
    chr(0x00A3): "GBP",  # pound sign
    chr(0x20B9): "INR",  # rupee sign
}
_CERTAIN_WORDS = (
    (re.compile(r"\b(?:DHS?|DIRHAMS?)\b", re.IGNORECASE), "AED"),
    (re.compile(r"US\$"), "USD"),
    (re.compile("د\\.?\\s?إ|درهم"), "AED"),  # Arabic: dirham abbreviation, dirham
    (re.compile("ر\\.?\\s?س"), "SAR"),  # Arabic: Saudi riyal abbreviation
)
# ISO 4217 codes seen on UAE purchase documents; uppercase only, to avoid matching words.
_ISO_CODES = re.compile(
    r"\b(AED|USD|EUR|GBP|INR|SAR|OMR|QAR|KWD|BHD|PKR|LKR|PHP|CNY|JPY|CHF|CAD|AUD|SGD|EGP|TRY)\b"
)
# Weak hints. "$": usually USD, but other dollars use it too. OCR models trained before 2025
# do not know the dirham sign and print the nearest letter they know: D, D-with-stroke (U+0110)
# or Eth (U+00D0). Only counted as a standalone letter directly before an amount with
# decimals ("D 1,050.10"); "ROAD 12" or a name like "Duc" are just letters.
_LIKELY_DIRHAM = re.compile(
    "(?<![A-Za-z])[D" + chr(0x0110) + chr(0x00D0) + r"]\s?(?=\d[\d,]*[.,]\d{2}(?!\d))"
)
ISO_CURRENCY = re.compile(r"[A-Z]{3}")


@dataclass(frozen=True, slots=True)
class CurrencyMarkers:
    certain: frozenset[str]  # printed unambiguously: a code, a word or an official sign
    likely: frozenset[str]  # a hint only: "$", or an OCR reading of the dirham sign

    @property
    def any(self) -> frozenset[str]:
        return self.certain | self.likely


def find_currencies(text: str) -> CurrencyMarkers:
    """Currency markers printed in `text`, as ISO codes."""
    certain = set(_ISO_CODES.findall(text))
    certain |= {code for symbol, code in _CERTAIN_SYMBOLS.items() if symbol in text}
    certain |= {code for pattern, code in _CERTAIN_WORDS if pattern.search(text)}
    likely: set[str] = set()
    if "$" in text.replace("US$", ""):
        likely.add("USD")
    if _LIKELY_DIRHAM.search(text):
        likely.add("AED")
    return CurrencyMarkers(frozenset(certain), frozenset(likely - certain))
