"""Photo quality (decision 30), on generated pages (never client data)."""

import json

import cv2
import numpy as np
import pytest

from app.pipeline.page_quality import (
    UNEVEN_LIGHT_FIX,
    PageQuality,
    measure,
    prepare_for_ocr,
    quality_issues,
)
from app.pipeline.read import PageText, read_pages
from app.pipeline.verify.issues import Severity
from tests.read_fixtures import digital_pdf, jpeg, page_png, scanned_pdf_with_text
from tests.test_read_pages import FakeOcr


@pytest.fixture
def sharp() -> np.ndarray:
    return cv2.imdecode(np.frombuffer(page_png(), np.uint8), cv2.IMREAD_COLOR)


def test_a_clear_page_has_no_problem(sharp: np.ndarray) -> None:
    quality = measure(sharp)

    assert not (quality.blurry or quality.dark or quality.shadowed)
    assert quality_issues([(1, quality)]) == []


def test_a_blurred_photo_is_blurry(sharp: np.ndarray) -> None:
    assert measure(cv2.GaussianBlur(sharp, (9, 9), 0)).blurry


def test_a_dark_photo_is_dark(sharp: np.ndarray) -> None:
    assert measure((sharp * 0.35).astype(np.uint8)).dark


def test_a_shadow_is_found_and_evened_out_before_ocr(sharp: np.ndarray) -> None:
    width = sharp.shape[1]
    shadow = (sharp * np.linspace(0.3, 1.0, width)[None, :, None]).astype(np.uint8)
    quality = measure(shadow)

    cleaned = prepare_for_ocr(shadow, quality)

    assert quality.shadowed
    assert measure(cleaned).uneven_light <= UNEVEN_LIGHT_FIX
    assert cleaned.shape == shadow.shape


def test_a_well_lit_page_is_never_changed_before_ocr(sharp: np.ndarray) -> None:
    assert prepare_for_ocr(sharp, measure(sharp)) is sharp


def test_a_blank_page_is_not_called_blurry() -> None:
    blank = np.full((800, 600, 3), 235, np.uint8)

    assert measure(blank).sharpness is None
    assert not measure(blank).blurry


def test_poor_pages_get_one_warning_each_naming_the_problems() -> None:
    blurry_and_dark = PageQuality(sharpness=100, brightness=80, uneven_light=20)
    fine = PageQuality(sharpness=5000, brightness=225, uneven_light=20)

    [issue] = quality_issues([(1, fine), (2, blurry_and_dark), (3, None)])

    assert (issue.code, issue.severity, issue.field) == ("photo_poor", Severity.WARNING, None)
    assert issue.message.startswith("Page 2 looks blurry and too dark")


def test_pictures_are_measured_digital_pages_are_not() -> None:
    def read(data: bytes, mime: str) -> PageText:
        [(_, text)] = read_pages(data, mime, lambda: FakeOcr(), max_pages=5, max_side=1500,
                                 min_text_chars=20)  # fmt: skip
        return text

    assert read(digital_pdf(), "application/pdf").quality is None
    assert read(scanned_pdf_with_text(), "application/pdf").quality is not None
    assert read(jpeg(800, 600), "image/jpeg").quality is not None


def test_quality_is_saved_with_the_page_and_old_pages_still_load() -> None:
    page = PageText(1, 100, 100, "ocr", [], PageQuality(1234.5, 220.0, 30.0))
    old = json.dumps({"page": 1, "width": 100, "height": 100, "source": "ocr", "spans": []})

    assert PageText.from_json(page.to_json()).quality == page.quality
    assert PageText.from_json(old.encode()).quality is None
