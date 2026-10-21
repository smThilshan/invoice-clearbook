"""Words grouped into printed lines, from the boxes the Read step saved. Shared by the TRN
finder (decision 29) and the field locator (task 4.6)."""

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
    """The page's words as lines, top to bottom, each line left to right."""
    rows: list[list[TextSpan]] = []
    for span in sorted(page.spans, key=centre):
        if rows and abs(centre(span) - centre(rows[-1][0])) <= LINE_TOLERANCE:
            rows[-1].append(span)
        else:
            rows.append([span])
    lines = []
    for row in rows:
        row.sort(key=lambda span: span.box[0])
        lines.append(
            Line(page.page, centre(row[0]), row[0].box[0], tuple(row), page.source == "ocr")
        )
    return lines


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
