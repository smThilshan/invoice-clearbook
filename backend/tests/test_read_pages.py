import pytest

from app.adapters.ocr import Image, OcrEngine, RecognizedText
from app.pipeline.read import PageText, TextSpan, UnreadableError, read_pages
from tests.read_fixtures import digital_pdf, jpeg, scanned_pdf


class FakeOcr:
    def __init__(self) -> None:
        self.calls = 0

    def recognize(self, image: Image) -> list[RecognizedText]:
        self.calls += 1
        height, width = image.shape[:2]
        return [
            RecognizedText("Total AED 1,050.10", 0.98, (0, 0, width / 2, height / 4)),
            RecognizedText("   ", 0.50, (0, 0, 1, 1)),  # blank spans are dropped
        ]


def read(
    data: bytes, mime: str, ocr: FakeOcr | None = None, **kw: int
) -> list[tuple[Image, PageText]]:
    engine = ocr or FakeOcr()
    options = {"max_pages": 20, "max_side": 1500, "min_text_chars": 20} | kw

    def provider() -> OcrEngine:
        return engine

    return read_pages(data, mime, provider, **options)


def test_digital_pdf_uses_text_layer_and_never_ocr() -> None:
    ocr = FakeOcr()

    [(image, text)] = read(digital_pdf(), "application/pdf", ocr)

    assert ocr.calls == 0
    assert text.source == "text_layer"
    assert "INV-00291" in text.text
    assert max(image.shape[:2]) == 1500
    assert all(span.confidence is None for span in text.spans)
    assert all(0 <= v <= 1 for span in text.spans for v in span.box)


def test_scanned_pdf_pages_go_through_ocr() -> None:
    ocr = FakeOcr()

    pages = read(scanned_pdf(pages=2), "application/pdf", ocr)

    assert ocr.calls == 2
    assert [text.page for _, text in pages] == [1, 2]
    assert all(text.source == "ocr" for _, text in pages)
    [span] = pages[0][1].spans
    assert span.box == (0.0, 0.0, 0.5, 0.25)  # pixel box -> fractions of the page
    assert span.confidence == 0.98


def test_photo_is_turned_upright_from_exif() -> None:
    [(image, text)] = read(jpeg(400, 200, exif_orientation=6), "image/jpeg")

    assert image.shape[:2] == (400, 200)  # was 400 wide x 200 high; now portrait
    assert text.source == "ocr"


def test_large_photo_shrinks_small_photo_never_grows() -> None:
    [(big, _)] = read(jpeg(3000, 2000), "image/jpeg")
    [(small, _)] = read(jpeg(800, 600), "image/jpeg")

    assert big.shape[:2] == (1000, 1500)
    assert small.shape[:2] == (600, 800)


@pytest.mark.parametrize(
    ("data", "mime", "message"),
    [
        (b"%PDF-1.7 truncated garbage", "application/pdf", "cannot open PDF"),
        (b"\xff\xd8\xff not really a jpeg", "image/jpeg", "cannot decode image"),
        (digital_pdf(pages=3), "application/pdf", "too many pages (3; limit 2)"),
    ],
)
def test_unreadable_files_raise_with_reason(data: bytes, mime: str, message: str) -> None:
    with pytest.raises(UnreadableError, match=message.replace("(", r"\(").replace(")", r"\)")):
        read(data, mime, max_pages=2)


def test_page_text_json_round_trip() -> None:
    page = PageText(1, 1000, 1400, "ocr", [TextSpan("Total", (0.1, 0.2, 0.3, 0.4), 0.9)])

    assert PageText.from_json(page.to_json()) == page
