"""Decision 67: a photo's tilt is measured, for grouping its words into lines; unsure is 0."""

import cv2
import numpy as np
import pytest

from app.adapters.ocr import Image
from app.pipeline.tilt import find_tilt


def slip(tilt: float = 0.0) -> Image:
    """A till slip: rows of black 'text' on white paper, turned by `tilt` degrees."""
    page = np.full((900, 500, 3), 255, dtype=np.uint8)
    for top in range(60, 840, 40):
        for left in range(40, 420, 70):
            cv2.rectangle(page, (left, top), (left + 50, top + 12), (0, 0, 0), -1)
    if not tilt:
        return page
    turn = cv2.getRotationMatrix2D((250, 450), tilt, 1.0)
    turned = cv2.warpAffine(page, turn, (500, 900), borderValue=(255, 255, 255))
    return np.asarray(turned, dtype=np.uint8)


@pytest.mark.parametrize("tilt", [1.5, -2.0, 4.0])
def test_a_tilted_slip_gives_the_angle_that_levels_it(tilt: float) -> None:
    assert find_tilt(slip(tilt)) == pytest.approx(-tilt, abs=0.2)


def test_a_level_slip_has_no_tilt() -> None:
    assert find_tilt(slip()) == 0.0


@pytest.mark.parametrize(
    ("page", "why"),
    [
        (np.full((900, 500, 3), 255, dtype=np.uint8), "a blank page: no rows to line up"),
        (slip(20.0), "turned further than a hand-held photo: not a small tilt"),
        (
            np.random.default_rng(1).integers(0, 256, (900, 500, 3), dtype=np.uint8),
            "noise: no angle is clearly better",
        ),
    ],
)
def test_nothing_is_turned_when_unsure(page: Image, why: str) -> None:
    assert find_tilt(page) == 0.0, why
