"""Line descriptions cleaned after extraction (decision 42). All items are made up."""

import json
from decimal import Decimal

import pytest

from app.domain.enums import DocumentType
from app.domain.invoice import Invoice, Line
from app.pipeline.descriptions import clean_descriptions, cleaned_count


def invoice(*lines: tuple[str | None, str | None]) -> Invoice:
    """Lines as (description, qty)."""
    return Invoice(
        document_type=DocumentType.BILL,
        lines=[Line(description=text, qty=Decimal(qty) if qty else None, amount=Decimal("1"))
               for text, qty in lines],
    )  # fmt: skip


def descriptions(*lines: tuple[str | None, str | None]) -> list[str | None]:
    cleaned, _ = clean_descriptions(invoice(*lines))
    return [line.description for line in cleaned.lines]


@pytest.mark.parametrize(
    ("text", "qty", "expected"),
    [
        ("5 x Sugar 1kg", "5", "Sugar 1kg"),
        ("3 X Apple Juice", "3", "Apple Juice"),
        ("2× Tea Bags", "2", "Tea Bags"),  # noqa: RUF001 - the multiplication sign, on purpose
        ("1.5 x Cheese block", "1.5", "Cheese block"),
        ("2 x Rice 5kg", "4", "2 x Rice 5kg"),  # numbers disagree: leave it to be seen
        ("2 x Rice 5kg", None, "2 x Rice 5kg"),  # no quantity to compare with
        ("12 Eggs Tray", "12", "12 Eggs Tray"),  # no "x": may be the name
        ("Box of 5 x 200g", "1", "Box of 5 x 200g"),  # not at the start
    ],
)
def test_a_quantity_prefix_goes_only_when_it_is_the_lines_quantity(
    text: str, qty: str | None, expected: str
) -> None:
    assert descriptions((text, qty)) == [expected]


def test_a_code_column_goes_when_every_line_starts_with_a_code() -> None:
    assert descriptions(("105 FULL CREAM MILK 2L", "6"), ("130 LOW FAT MILK 2L", "1")) == [
        "FULL CREAM MILK 2L", "LOW FAT MILK 2L"]  # fmt: skip


@pytest.mark.parametrize(
    "lines",
    [
        [("105 Fresh Milk", "1"), ("Yoghurt cup", "2")],  # not every line: no code column
        [("105 Fresh Milk", "1")],  # one line: a number could be the name
        [("500 ml Water", "1"), ("250 ml Juice", "2")],  # sizes, not codes
        [("500 Paper cups", "1"), ("200 G Sugar", "1")],  # one size spoils the column
        [("12 Eggs", "1"), ("30 Eggs", "1")],  # two digits: not a code
    ],
)
def test_numbers_that_may_belong_to_the_name_stay(lines: list[tuple[str, str]]) -> None:
    assert descriptions(*lines) == [text for text, _ in lines]


def test_both_at_once_and_the_count_of_changed_lines() -> None:
    cleaned, changed = clean_descriptions(
        invoice(("2 x 105 Full Cream Milk", "2"), ("130 Low Fat Milk", "1"), (None, "1"))
    )

    assert [line.description for line in cleaned.lines] == [
        "105 Full Cream Milk", "130 Low Fat Milk", None]  # fmt: skip
    assert changed == 1  # the empty line breaks the code column: codes stay


def test_nothing_to_clean_returns_the_same_invoice() -> None:
    original = invoice(("Sugar 1kg", "5"))

    assert clean_descriptions(original) == (original, 0)


def test_the_review_screen_count_compares_the_models_answer_with_what_was_kept() -> None:
    raw = json.dumps({"lines": [{"description": "5 x Sugar"}, {"description": "Tea"}]})
    data = {"lines": [{"description": "Sugar"}, {"description": "Tea"}]}

    assert cleaned_count(raw, data) == 1
    assert cleaned_count("not json", data) == 0
    assert cleaned_count(raw, None) == 0
