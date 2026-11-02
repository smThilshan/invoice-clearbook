"""Decision 67: words grouped into printed lines on tilted and curled photos."""

from app.pipeline.read import PageText, TextSpan
from app.pipeline.verify.layout import page_lines


def photo(*spans: TextSpan, tilt: float = 0.0) -> PageText:
    return PageText(1, 600, 1500, "ocr", list(spans), tilt=tilt)


def word(text: str, x0: float, x1: float, middle: float) -> TextSpan:
    return TextSpan(text, (x0, middle - 0.01, x1, middle + 0.01), 0.9)


def test_a_line_drifting_on_curled_paper_is_followed_word_by_word() -> None:
    """Al Shahama 701 as OCR placed it: "3.33" 0.0067 below "Total Qty", 0.0014 below its label."""
    page = photo(
        TextSpan("Total Qty: 1.00", (0.042, 0.3853, 0.335, 0.4107), 0.9),
        TextSpan("Total Amount:", (0.515, 0.3927, 0.786, 0.4140), 0.9),
        TextSpan("3.33", (0.851, 0.3947, 0.942, 0.4147), 0.9),
        TextSpan("Total Tax:", (0.30, 0.4487, 0.55, 0.4707), 0.9),
        TextSpan("0.17", (0.62, 0.4520, 0.72, 0.4720), 0.9),
    )

    assert [line.text for line in page_lines(page)] == [
        "Total Qty: 1.00 Total Amount: 3.33",
        "Total Tax: 0.17",
    ]


def test_a_tilted_line_with_nothing_between_its_ends_is_read_level() -> None:
    """The amount far to the right sits 13 px lower: only the photo's tilt explains it."""
    label, amount = word("Net Amount:", 0.05, 0.35, 0.500), word("3.50", 0.85, 0.95, 0.509)

    assert [line.text for line in page_lines(photo(label, amount))] == ["Net Amount:", "3.50"]
    levelled = page_lines(photo(label, amount, tilt=1.8))
    assert [line.text for line in levelled] == ["Net Amount: 3.50"]
    assert levelled[0].spans[1].box == amount.box  # boxes as read, for highlighting


def test_close_lines_stay_apart() -> None:
    page = photo(word("Tendered", 0.05, 0.4, 0.500), word("Balance", 0.05, 0.4, 0.512))

    assert [line.text for line in page_lines(page)] == ["Tendered", "Balance"]
