"""Real PaddleOCR on a generated image (~20 s: model load). Needs the models (downloaded once)."""

import cv2
import numpy as np
import pytest

from app.adapters.ocr import get_ocr_engine
from tests.read_fixtures import page_png

pytestmark = pytest.mark.integration


def test_paddle_ocr_reads_generated_invoice_text() -> None:
    image = cv2.imdecode(np.frombuffer(page_png(), dtype=np.uint8), cv2.IMREAD_COLOR)

    results = get_ocr_engine().recognize(np.asarray(image, dtype=np.uint8))

    texts = [r.text for r in results]
    assert "Invoice No: INV-00291" in texts
    assert any("1,050.10" in t for t in texts)
    assert all(r.confidence > 0.8 for r in results)
    height, width = image.shape[:2]
    assert all(0 <= r.box[0] < r.box[2] <= width and 0 <= r.box[1] < r.box[3] <= height
               for r in results)  # fmt: skip
