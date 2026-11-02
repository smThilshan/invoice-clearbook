"""Words grouped into printed lines, from the boxes the Read step saved. Shared by the TRN
finder (decision 29) and the field locator (task 4.6)."""

import math
from collections.abc import Callable
from dataclasses import dataclass

from app.pipeline.read import PageText, TextSpan
from app.pipeline.verify.normalize import to_ascii_digits

# Same line: vertical centres closer than this (fraction of the page height).
LINE_TOLERANCE = 0.006


@dataclass(frozen=True, slots=True)
class Line:
    page: int
    y: float  # vertical centre
    x: float  # left edge
    spans: tuple[TextSpan, ...]  # left to right
    ocr: bool  # read by OCR (ours or a scanner's): may misread characters

    @property
    def text(self) -> str:
        return to_ascii_digits(" ".join(span.text for span in self.spans))


def page_lines(page: PageText) -> list[Line]:
    """The page's words as lines, top to bottom, each line left to right (decision 67): heights
    as if the photo were level, and a word joins a line when it is at the height of the word
    of that line nearest to it, so a line that still drifts (curled paper) is followed word by
    word. The words keep their own boxes, for highlighting."""
    level = _leveller(page)
    rows: list[list[TextSpan]] = []
    for span in sorted(page.spans, key=level):
        if rows and abs(level(span) - level(_nearest(rows[-1], span))) <= LINE_TOLERANCE:
            rows[-1].append(span)
        else:
            rows.append([span])
    lines = []
    for row in rows:
        row.sort(key=lambda span: span.box[0])
        lines.append(
            Line(page.page, level(row[0]), row[0].box[0], tuple(row), page.source == "ocr")
        )
    return lines


def _leveller(page: PageText) -> Callable[[TextSpan], float]:
    """A word's vertical centre with the page turned level by its tilt (fraction of the height)."""
    if not page.tilt:
        return centre
    turn = math.radians(page.tilt)
    sin, cos = math.sin(turn), math.cos(turn)
    width, height = page.width, page.height

    def level(span: TextSpan) -> float:
        x = ((span.box[0] + span.box[2]) / 2 - 0.5) * width  # pixels from the page centre
        y = (centre(span) - 0.5) * height
        return (cos * y - sin * x) / height + 0.5

    return level


def _nearest(row: list[TextSpan], span: TextSpan) -> TextSpan:
    """The word of the row horizontally nearest to `span` (0 when they overlap)."""
    return min(row, key=lambda word: max(word.box[0] - span.box[2], span.box[0] - word.box[2], 0.0))


def centre(span: TextSpan) -> float:
    return (span.box[1] + span.box[3]) / 2


def union_box(spans: tuple[TextSpan, ...]) -> tuple[float, float, float, float]:
    """The smallest box around all the spans (fractions of the page)."""
    return (
        min(span.box[0] for span in spans),
        min(span.box[1] for span in spans),
        max(span.box[2] for span in spans),
        max(span.box[3] for span in spans),
    )
