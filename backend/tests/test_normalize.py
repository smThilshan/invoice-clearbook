from datetime import date
from decimal import Decimal

import pytest

from app.pipeline.verify.normalize import (
    find_amounts,
    find_dates,
    is_valid_trn,
    normalize_invoice_number,
    normalize_trn,
    parse_amount,
    parse_date,
    to_ascii_digits,
)


def arabic(text: str) -> str:
    """Write ASCII digits/separators as their Arabic-Indic forms (test helper)."""
    table = {str(n): chr(0x0660 + n) for n in range(10)} | {".": chr(0x066B), ",": chr(0x066C)}
    return "".join(table.get(ch, ch) for ch in text)


# --- digits ------------------------------------------------------------------------------


def test_arabic_indic_and_extended_digits_become_ascii() -> None:
    extended = "".join(chr(0x06F0 + n) for n in range(10))

    assert to_ascii_digits(arabic("1,050.10")) == "1,050.10"
    assert to_ascii_digits(extended) == "0123456789"
    assert to_ascii_digits(chr(0x2212) + "50") == "-50"


# --- amounts -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1,050.10", "1050.10"),
        ("AED 1,050.10", "1050.10"),
        ("Total: 1,050.10 AED", "1050.10"),
        ("1050.10", "1050.10"),
        ("105", "105"),
        ("0.50", "0.50"),
        ("1,234,567.89", "1234567.89"),
        ("1,050", "1050"),  # comma + 3 digits = thousands (UAE)
        ("1.050", "1.050"),  # dot = decimal; 3 decimals are valid (KWD/BHD/OMR)
        ("50,10", "50.10"),  # decimal comma
        ("1.050,10", "1050.10"),  # both: the last one is the decimal mark
        ("1.234.567", "1234567"),
        ("(50.00)", "-50.00"),
        ("-50.00", "-50.00"),
        ("50.00-", "-50.00"),
        ("AED -1,050.10", "-1050.10"),
        (arabic("1,050.10"), "1050.10"),
    ],
)
def test_parse_amount(text: str, expected: str) -> None:
    assert parse_amount(text) == Decimal(expected)


@pytest.mark.parametrize(
    "text",
    ["", "AED", "N/A", "5% of 100.00", "1,05,0", "12,3456", "1,050.10.20"],
)
def test_parse_amount_rejects_no_or_unclear_numbers(text: str) -> None:
    assert parse_amount(text) is None


def test_parse_amount_keeps_exact_decimal_places() -> None:
    assert str(parse_amount("1,050.10")) == "1050.10"  # text value, never a float


def test_find_amounts_returns_every_number_unsigned() -> None:
    text = "Sub Total 1,000.10\nVAT 5% 50.00\nTotal AED 1,050.10\nCredit (25.00)"

    assert find_amounts(text) == {
        Decimal("1000.10"),
        Decimal("5"),
        Decimal("50.00"),
        Decimal("1050.10"),
        Decimal("25.00"),
    }


def test_find_amounts_reads_arabic_digits() -> None:
    assert Decimal("1050.10") in find_amounts(f"Total {arabic('1,050.10')}")


# --- dates -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2025-09-12", date(2025, 9, 12)),
        ("2025/09/12", date(2025, 9, 12)),
        ("12 Sep 2025", date(2025, 9, 12)),
        ("12-Sep-2025", date(2025, 9, 12)),
        ("12-SEP-25", date(2025, 9, 12)),
        ("12 September 2025", date(2025, 9, 12)),
        ("12th Sept, 2025", date(2025, 9, 12)),
        ("Sep 12, 2025", date(2025, 9, 12)),
        ("Date: 25/09/2025", date(2025, 9, 25)),  # unambiguous: 25 cannot be a month
        ("25.09.25", date(2025, 9, 25)),
        (arabic("25/09/2025"), date(2025, 9, 25)),
    ],
)
def test_parse_date_unambiguous(text: str, expected: date) -> None:
    reading = parse_date(text)

    assert reading is not None
    assert (reading.value, reading.ambiguous) == (expected, False)


def test_numeric_date_that_reads_both_ways_is_flagged_not_guessed() -> None:
    reading = parse_date("02/09/2025")

    assert reading is not None
    assert reading.value == date(2025, 9, 2)  # UAE: day first
    assert reading.ambiguous is True
    assert reading.alternative == date(2025, 2, 9)


def test_same_day_and_month_is_not_ambiguous() -> None:
    reading = parse_date("05/05/2025")

    assert reading is not None and not reading.ambiguous


@pytest.mark.parametrize("text", ["", "no date here", "31/02/2025", "12 Foo 2025", "INV-0291"])
def test_parse_date_rejects_non_dates(text: str) -> None:
    assert parse_date(text) is None


def test_find_dates_includes_both_readings_of_ambiguous_dates() -> None:
    text = "Invoice date 02/09/2025\nDue 30 Sep 2025\nTRN 100999000000001"

    assert find_dates(text) == {date(2025, 9, 2), date(2025, 2, 9), date(2025, 9, 30)}


# --- invoice numbers ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "printed",
    ["INV-00291", "inv 291", "#291", "291", "00291", "Invoice No: 291", "INVOICE NO. 00291",
     "Tax Invoice No 291", "Inv#291", arabic("291")],
)  # fmt: skip
def test_invoice_number_variants_match(printed: str) -> None:
    assert normalize_invoice_number(printed) == "291"  # architecture §10 examples


@pytest.mark.parametrize(
    ("printed", "expected"),
    [
        ("SI-2025-0045", "SI20250045"),  # letters first: zeros kept, only symbols removed
        ("2025/0045", "20250045"),
        ("A-12", "A12"),
        ("0", "0"),
    ],
)
def test_invoice_number_other_shapes(printed: str, expected: str) -> None:
    assert normalize_invoice_number(printed) == expected


@pytest.mark.parametrize("printed", ["", "  ", "INV-", "#", "Invoice No:"])
def test_invoice_number_without_a_number_is_none(printed: str) -> None:
    assert normalize_invoice_number(printed) is None


# --- TRN ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "printed",
    ["100999000000001", "100 999 000 000 001", "100-9990-0000-0001", "TRN: 100999000000001",
     arabic("100999000000001")],
)  # fmt: skip
def test_trn_normalized_to_digits(printed: str) -> None:
    digits = normalize_trn(printed)

    assert digits == "100999000000001"
    assert is_valid_trn(digits)


@pytest.mark.parametrize("digits", ["", "10099900000000", "1009990000000012", "10099900000000A"])
def test_trn_must_be_exactly_15_digits(digits: str) -> None:
    assert not is_valid_trn(digits)
