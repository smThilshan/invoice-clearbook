"""Decision 62: amounts OCR split in two are joined when saved page text is loaded."""

import json
from dataclasses import asdict

import pytest

from app.pipeline.read import PageText, TextSpan, join_split_numbers

ROW = 0.49  # top of the row, as a fraction of the page height
HEIGHT = 0.017


def piece(text: str, x0: float, x1: float, top: float = ROW) -> TextSpan:
    return TextSpan(text, (x0, top, x1, top + HEIGHT), 0.9)


def page(*spans: TextSpan, source: str = "ocr") -> PageText:
    # Al Shahama slip 387: 1000 x 2000 px, so one character height is 34 px
    return PageText(1, 1000, 2000, source, list(spans))  # type: ignore[arg-type]


def texts(result: PageText) -> list[str]:
    return [span.text for span in result.spans]


@pytest.mark.parametrize(
    ("left", "right", "joined"),
    [
        ("o.", "12", "0.12"),  # Total Tax : 0.12
        ("-o", ".09", "-0.09"),  # Rounding : -0.09
        ("O.", "16", "0.16"),
        ("31", ".75", "31.75"),
        ("6,", "99", "6,99"),  # decimal comma: read as 6.99 by the checks
    ],
)
def test_an_amount_cut_in_two_is_joined(left: str, right: str, joined: str) -> None:
    result = join_split_numbers(page(piece("Rounding", 0.5, 0.6), piece(left, 0.859, 0.893),
                                     piece(right, 0.900, 0.951)))  # fmt: skip

    assert texts(result) == ["Rounding", joined]
    assert result.spans[1].box == (0.859, ROW, 0.951, ROW + HEIGHT)  # highlights both pieces


@pytest.mark.parametrize(
    ("spans", "why"),
    [
        ([piece("1.00", 0.6, 0.67), piece("2.59", 0.69, 0.76)], "two amounts side by side"),
        ([piece("o.", 0.5, 0.53), piece("12", 0.80, 0.84)], "far apart: two things"),
        ([piece("o.", 0.5, 0.53), piece("12", 0.535, 0.57, top=ROW + 0.03)], "another row"),
        ([piece("Qty", 0.5, 0.55), piece("12", 0.56, 0.6)], "a word, not a number"),
        ([piece("1", 0.5, 0.52), piece("12", 0.53, 0.57)], "no decimal point: 112"),
        ([piece("2.", 0.5, 0.53), piece("125", 0.54, 0.6)], "three decimals: not money"),
    ],
)
def test_nothing_else_is_joined(spans: list[TextSpan], why: str) -> None:
    assert texts(join_split_numbers(page(*spans))) == [span.text for span in spans], why


def test_the_text_layer_of_a_digital_pdf_is_never_changed() -> None:
    spans = [piece("o.", 0.859, 0.893), piece("12", 0.900, 0.951)]

    assert texts(join_split_numbers(page(*spans, source="text_layer"))) == ["o.", "12"]


def test_saved_ocr_text_is_joined_when_loaded_and_kept_as_read_on_disk() -> None:
    saved = page(piece("o.", 0.859, 0.893), piece("12", 0.900, 0.951))
    data = json.dumps(asdict(saved)).encode()

    assert texts(PageText.from_json(data)) == ["0.12"]
    assert [s["text"] for s in json.loads(data)["spans"]] == ["o.", "12"]
