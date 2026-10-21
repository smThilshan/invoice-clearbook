"""Photo quality (decision 30): most purchase documents are phone photos, often saved as PDF.

Measured on every page that is a picture (photo, or a PDF page that is a scan), on the rendered
page image, in a few milliseconds:

- sharpness: Laplacian variance in the 10% sharpest areas that contain ink, so a page with
  little text is not called blurry because most of it is paper;
- brightness: mean grey level;
- uneven light: spread of the paper's brightness across the page (a shadow, a dark corner).

Thresholds from the first real run (EXP-002, ROAST): sharp invoices 4,700-6,600, a soft thermal
receipt 1,354, the one unreadable photo 166; the same invoice blurred lightly/clearly/heavily
1,161/485/106. Paper 19-41 uneven; with a side shadow 140. Normal brightness ~225; darkened 80.

A poor page is flagged for the accountant (a warning: values may be misread). Before OCR, uneven
or dark pages get their light evened out; the original image is kept for the model and the viewer.
"""

from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import Any

import cv2
import numpy as np

from app.adapters.ocr import Image
from app.pipeline.verify.issues import Issue, Severity

SHARPNESS_MIN = 500.0
BRIGHTNESS_MIN = 110.0
UNEVEN_LIGHT_FLAG = 100.0  # warn: a visible shadow
UNEVEN_LIGHT_FIX = 60.0  # even out the light before OCR

_TILE = 64  # px, on the rendered page (longest side 1500)
_INK_STD = 20.0  # a tile whose grey levels vary this much holds text or lines


@dataclass(frozen=True, slots=True)
class PageQuality:
    sharpness: float | None  # None: no text on the page to judge
    brightness: float
    uneven_light: float

    @property
    def blurry(self) -> bool:
        return self.sharpness is not None and self.sharpness < SHARPNESS_MIN

    @property
    def dark(self) -> bool:
        return self.brightness < BRIGHTNESS_MIN

    @property
    def shadowed(self) -> bool:
        return self.uneven_light > UNEVEN_LIGHT_FLAG

    def to_json(self) -> dict[str, Any]:
        return {k: None if v is None else round(v, 1) for k, v in asdict(self).items()}


def measure(image: Image) -> PageQuality:
    grey = _grey(image)
    return PageQuality(
        sharpness=_text_sharpness(grey),
        brightness=float(grey.mean()),
        uneven_light=_uneven_light(grey),
    )


def prepare_for_ocr(image: Image, quality: PageQuality) -> Image:
    """Even out shadows and dark photos (divide by the paper's own brightness); a well lit page
    is returned unchanged, so good photos are never made worse."""
    if quality.uneven_light <= UNEVEN_LIGHT_FIX and not quality.dark:
        return image
    grey = _grey(image)
    paper = cv2.medianBlur(cv2.dilate(grey, np.ones((7, 7), np.uint8)), 21)
    flat = cv2.divide(grey, paper, scale=255)
    stretched = cv2.normalize(flat, np.empty_like(flat), 0, 255, cv2.NORM_MINMAX)
    return np.asarray(cv2.cvtColor(stretched, cv2.COLOR_GRAY2BGR), dtype=np.uint8)


def quality_issues(qualities: Sequence[tuple[int, PageQuality | None]]) -> list[Issue]:
    """Warnings for poor pages, as (page number, quality); None = digital page, not a photo."""
    issues = []
    for page, quality in qualities:
        if quality is None:
            continue
        problems = [
            word
            for word, bad in (
                ("blurry", quality.blurry),
                ("too dark", quality.dark),
                ("in shadow", quality.shadowed),
            )
            if bad
        ]
        if problems:
            issues.append(
                Issue(
                    "photo_poor",
                    Severity.WARNING,
                    None,
                    f"Page {page} looks {' and '.join(problems)}: values may be misread. Check "
                    "them against the original, or ask the restaurant for a clearer copy.",
                )
            )
    return issues


def _grey(image: Image) -> np.ndarray[Any, np.dtype[np.uint8]]:
    if image.ndim == 2:
        return image
    return np.asarray(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY), dtype=np.uint8)


def _text_sharpness(grey: np.ndarray[Any, np.dtype[np.uint8]]) -> float | None:
    laplacian = cv2.Laplacian(grey, cv2.CV_64F)
    height, width = grey.shape
    values = [
        float(laplacian[y : y + _TILE, x : x + _TILE].var())
        for y in range(0, height - _TILE + 1, _TILE)
        for x in range(0, width - _TILE + 1, _TILE)
        if grey[y : y + _TILE, x : x + _TILE].std() > _INK_STD
    ]
    return float(np.percentile(values, 90)) if values else None


def _uneven_light(grey: np.ndarray[Any, np.dtype[np.uint8]]) -> float:
    small = cv2.resize(grey, (64, 64), interpolation=cv2.INTER_AREA)
    paper = cv2.dilate(small, np.ones((7, 7), np.uint8))  # ink removed, paper level left
    return float(np.percentile(paper, 95) - np.percentile(paper, 5))
