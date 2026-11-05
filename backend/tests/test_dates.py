"""The document date from every clue (decision 85). Pages as our OCR read them; dates made up
where said."""

from datetime import date

import pytest

from app.pipeline.dates import (
    DateDecision,
    DateLimits,
    DateOrder,
    date_order,
    photo_date,
    resolve_date,
)
from app.pipeline.read import PageText, TextSpan

TODAY = date(2026, 11, 5)
LIMITS = DateLimits(today=TODAY, future_slack_days=1, max_days_before_folder=90)


def page(text: str) -> list[PageText]:
    rows = text.splitlines()
    spans = [TextSpan(row, (0.0, i / len(rows), 1.0, (i + 0.5) / len(rows)), None)
             for i, row in enumerate(rows)]  # fmt: skip
    return [PageText(1, 1000, 1400, "ocr", spans)]


def resolve(
    text: str,
    model: date | None = None,
    quoted: str | None = None,
    limits: DateLimits = LIMITS,
    order: DateOrder | None = None,
) -> DateDecision | None:
    return resolve_date(model, quoted, page(text), limits, order)


BLUE_TOWER = """BLUE TOWER FACILITIES
TRN: 100123456700003
Bill To: ROAST Specialty Coffee (Cha Cha Chai)
Receipt Date: March 5, 2026
Total AED 52.50"""


def test_a_date_the_model_missed_is_read_after_its_label() -> None:
    found = resolve(BLUE_TOWER)

    assert found is not None and found.value == date(2026, 3, 5)
    assert "Receipt Date" in found.reason


def test_a_date_read_the_wrong_way_round_is_read_month_first_when_only_that_fits() -> None:
    """GALA: "03/12/26" read day first is 3 December, in the future; 12 March fits."""
    found = resolve("GALA\n03/12/26 14:07", model=date(2026, 12, 3), quoted="03/12/26")

    assert found is not None
    assert (found.value, found.alternative) == (date(2026, 3, 12), date(2026, 12, 3))


def test_a_date_that_fits_both_ways_stays_as_read_unless_the_supplier_shows_its_order() -> None:
    model, quoted = date(2026, 4, 3), "03/04/26"  # 3 April or 4 March: both in the past

    assert resolve("Date 03/04/26", model, quoted) is None
    assert resolve("Date 03/04/26", model, quoted, order=DateOrder.DAY_FIRST) is None
    found = resolve("Date 03/04/26", model, quoted, order=DateOrder.MONTH_FIRST)
    assert found is not None and found.value == date(2026, 3, 4)


def test_the_folder_chooses_between_the_two_readings() -> None:
    limits = DateLimits(today=TODAY, future_slack_days=1, folder=date(2026, 3, 11),
                        max_days_before_folder=90)  # fmt: skip

    found = resolve("Date 03/04/26", date(2026, 4, 3), "03/04/26", limits)

    assert found is not None and found.value == date(2026, 3, 4)  # April is after March


def test_the_folder_never_replaces_a_printed_date() -> None:
    """An old receipt filed late: its date is long before the folder's, and stays."""
    limits = DateLimits(today=TODAY, future_slack_days=1, folder=date(2026, 10, 20),
                        max_days_before_folder=90)  # fmt: skip

    assert resolve("Date: 05-Jan-2026", date(2026, 1, 5), "05-Jan-2026", limits) is None


def test_a_photo_cannot_be_taken_before_the_receipt_was_printed() -> None:
    limits = DateLimits(today=TODAY, future_slack_days=1, photo=date(2025, 5, 10))

    found = resolve("05/08/25", date(2025, 8, 5), "05/08/25", limits)

    assert found is not None and found.value == date(2025, 5, 8)


@pytest.mark.parametrize(
    "text",
    ["Printed: 16-Mar-25 11:38 am", "Due Date: 14/11/2026", "Delivery 20 Oct 2026",
     "Valid until 30 Nov 2026"],
)  # fmt: skip
def test_dates_after_other_labels_are_never_the_document_date(text: str) -> None:
    assert resolve(f"AMEXHUB FOOD TRADING\n{text}") is None


def test_a_date_standing_alone_takes_the_label_above_it() -> None:
    found = resolve("Invoice No 77\nDate\n25/04/2026\nTotal 3.50")

    assert found is not None and found.value == date(2026, 4, 25)
    assert "Date" in found.reason


def test_a_page_date_that_reads_two_ways_is_not_guessed() -> None:
    assert resolve("Date\n05/04/2026") is None  # 5 April or 4 May
    found = resolve("Date\n05/04/2026", order=DateOrder.DAY_FIRST)
    assert found is not None and found.value == date(2026, 4, 5)


def test_the_only_date_on_the_page_is_used_without_a_label() -> None:
    found = resolve("AL SHAHAMA SUPERMARKET\n:05-Apr-2026 14:09\nNet Amount 3.50")

    assert found is not None and found.value == date(2026, 4, 5)
    assert found.reason == "the only date printed on the page"


def test_two_different_dates_are_left_to_the_accountant() -> None:
    assert resolve("Date: 05 Apr 2026\nDate: 07 Apr 2026") is None
    assert resolve("05 Apr 2026\n07 Apr 2026") is None


def test_a_model_date_that_fits_stands() -> None:
    assert resolve(BLUE_TOWER, date(2026, 3, 5), "March 5, 2026") is None


def test_a_model_date_in_the_future_gives_way_to_the_printed_one() -> None:
    found = resolve("Date: 05 Apr 2026", date(2027, 4, 5), "05 Apr 2027")

    assert found is not None and found.value == date(2026, 4, 5)


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Developer/August/IMG-20250815-WA0017.jpg", date(2025, 8, 15)),
        ("Scan - 2026-02-27 11_48_07.pdf", date(2026, 2, 27)),
        ("PXL_20260401_101500.jpg", date(2026, 4, 1)),
        ("INV-20250045.pdf", None),  # no such month
        ("IMG-20271201.jpg", None),  # after today: not a photo date
        ("2026-01-20/inv.pdf", None),  # in a folder name, not the file's
        ("gala.pdf", None),
    ],
)
def test_the_photo_date_comes_from_the_file_name(name: str, expected: date | None) -> None:
    assert photo_date(name, TODAY) == expected


def test_the_supplier_s_date_order_needs_dates_that_read_one_way() -> None:
    assert date_order(["25/03/26", "02/04/26", None]) is DateOrder.DAY_FIRST
    assert date_order(["03/25/26", "04/02/26"]) is DateOrder.MONTH_FIRST
    assert date_order(["02/04/26", "05-Apr-2026"]) is None  # nothing proves it
    assert date_order(["25/03/26", "03/25/26"]) is None  # both seen: not known
