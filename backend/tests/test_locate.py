"""Where a value is printed (task 4.6). Layouts like real invoices; values made up."""

from typing import Literal

import pytest

from app.pipeline.read import PageText, TextSpan
from app.pipeline.verify.locate import Match, ValueKind, locate

WORD_WIDTH = 0.05
GAP = 0.01


def page(*lines: tuple[float, str], number: int = 1,
         source: Literal["text_layer", "ocr"] = "text_layer") -> PageText:  # fmt: skip
    """Lines as (y, text); each word its own span, left to right from x = 0.1."""
    spans = []
    for y, text in lines:
        for n, word in enumerate(text.split()):
            left = round(0.1 + n * (WORD_WIDTH + GAP), 4)
            spans.append(TextSpan(word, (left, y, round(left + WORD_WIDTH, 4), y + 0.01), None))
    return PageText(number, 1000, 1400, source, spans)


def word_box(y: float, first: int, last: int) -> tuple[float, float, float, float]:
    """The box around words first..last (0-based) of a line built by `page`."""
    return (
        round(0.1 + first * (WORD_WIDTH + GAP), 4),
        y,
        round(0.1 + last * (WORD_WIDTH + GAP) + WORD_WIDTH, 4),
        y + 0.01,
    )


INVOICE = page(
    (0.10, "TAX INVOICE"),
    (0.14, "VAT REGISTRATION NO: 100 1112 2233 3003"),
    (0.20, "Invoice No: INV-00291 Date: 4/5/2026 (06:59)"),
    (0.60, "Sub Total 1,000.00"),
    (0.62, "VAT 5% 50.00"),
    (0.64, "Total AED 1,050.00"),
    (0.90, "Amount due 1050.00"),
)


@pytest.mark.parametrize(
    ("kind", "value", "expected"),
    [
        (ValueKind.AMOUNT, "1050", [word_box(0.64, 2, 2), word_box(0.90, 2, 2)]),  # printed twice
        (ValueKind.AMOUNT, "50.00", [word_box(0.62, 2, 2)]),
        (ValueKind.DATE, "2026-05-04", [word_box(0.20, 4, 4)]),  # 4/5/2026, time after it
        (ValueKind.TRN, "100111222333003", [word_box(0.14, 3, 6)]),  # printed in groups
        (ValueKind.TEXT, "INV 00291", [word_box(0.20, 2, 2)]),  # spaces and dashes ignored
        (ValueKind.TEXT, "tax invoice", [word_box(0.10, 0, 1)]),
    ],
)
def test_values_are_found_where_printed(
    kind: ValueKind, value: str, expected: list[tuple[float, float, float, float]]
) -> None:
    assert locate([INVOICE], kind, value) == [Match(1, box) for box in expected]


@pytest.mark.parametrize(
    ("kind", "value"),
    [
        (ValueKind.AMOUNT, "1060.00"),  # not printed
        (ValueKind.AMOUNT, "abc"),  # being typed: not a number yet
        (ValueKind.DATE, "2026-06-01"),
        (ValueKind.DATE, "2026-05"),
        (ValueKind.TEXT, "A"),  # too short: would match everywhere
        (ValueKind.TRN, "100999888777003"),
    ],
)
def test_values_not_printed_are_not_found(kind: ValueKind, value: str) -> None:
    assert locate([INVOICE], kind, value) == []


def test_ocr_misreads_are_tolerated_on_ocr_pages_only() -> None:
    lines = ((0.3, "TRN : 10044455S666OO3"), (0.5, "Total 1,O5O.00"))

    on_ocr = page(*lines, source="ocr")
    exact = page(*lines, source="text_layer")

    assert locate([on_ocr], ValueKind.TRN, "100444555666003") == [Match(1, word_box(0.3, 2, 2))]
    assert locate([on_ocr], ValueKind.AMOUNT, "1050") == [Match(1, word_box(0.5, 1, 1))]
    assert locate([exact], ValueKind.TRN, "100444555666003") == []


def test_a_day_month_date_matches_both_readings_like_grounding() -> None:
    # 4/5/2026 may be 4 May or 5 April: grounding accepts both, so the highlight does too
    assert locate([INVOICE], ValueKind.DATE, "2026-04-05") == [Match(1, word_box(0.20, 4, 4))]


def test_matches_on_every_page() -> None:
    first = page((0.2, "Invoice INV-7"))
    second = page((0.8, "Ref INV-7"), number=2)

    assert [m.page for m in locate([first, second], ValueKind.TEXT, "INV-7")] == [1, 2]


def test_names_tidied_by_the_model_are_found_approximately() -> None:
    printed = page((0.08, "AL RAWABI DAIRY CO L.L.C"), (0.12, "DAIRY DIVISION"))

    [match] = locate([printed], ValueKind.TEXT, "Al Rawabi Dairy Company LLC")

    assert (match.page, match.exact) == (1, False)
    assert match.box == word_box(0.08, 1, 4)  # RAWABI .. L.L.C


def test_scanner_misspellings_still_match_approximately() -> None:
    printed = page((0.14, "VAT REGISTRALION NO"), source="ocr")

    [match] = locate([printed], ValueKind.TEXT, "VAT Registration")

    assert match.exact is False


def test_exact_matches_win_and_single_words_never_match_approximately() -> None:
    assert all(m.exact for m in locate([INVOICE], ValueKind.TEXT, "tax invoice"))
    assert locate([INVOICE], ValueKind.TEXT, "Invoise") == []
