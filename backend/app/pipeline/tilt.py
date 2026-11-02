"""The tilt of a photo (decision 67), for grouping its words into lines as if it were level.

Phone photos of till slips are often a few degrees off. A printed line then drifts down across
the page, and grouping words by height splits it in two ("Total Amount :" on one line, its
"3.33" just lower on the next): every check that pairs a label with its amount misses it.
Sideways photos are already turned upright (EXIF); this is the small tilt that remains.

The picture itself is never turned: redrawing thin thermal print softened it, and OCR then
misread characters it had read right (a TRN lost a digit, "1.67" became "T.67"). The tilt is
only measured and saved with the page text; the line grouping (`verify/layout.py`) turns the
word positions instead.

The tilt is the angle at which the text rows line up best: on a small black-and-white copy, the
ink per pixel row varies most when the rows are level (dense text rows, empty gaps between).
Coarse steps, then fine ones around the best. Only a clear gain counts: a page whose rows line
up no better tilted than level, or tilted further than a hand-held photo is, gets 0.
"""

import cv2
import numpy as np

from app.adapters.ocr import Image

MAX_TILT = 6.0  # degrees; a photo more crooked than this is not a small tilt
MIN_TILT = 0.3  # degrees; below this a line does not drift by a row
# The rows must line up at least 5% better tilted than level. On the first 366 OCR pages, the
# best angle of pages under 5% was scattered (5.6, -3.1, 3.6 degrees): noise, not a tilt. Al
# Shahama 701, a line drifting 10 px across: 1.6 degrees, 9.6% better.
MIN_GAIN = 1.05
_WORK_WIDTH = 500  # px: the angle is found on a small copy, fast
# Searched wider than acted on: a page turned further would otherwise line up best at the edge
# of the range, a wrong angle.
_SEARCH = 15.0
_COARSE = 1.0
_FINE = 0.1


def find_tilt(image: Image) -> float:
    """The angle in degrees (counter-clockwise) that levels the text rows; 0.0 when unsure."""
    ink = _ink(image)
    level = _row_contrast(ink, 0.0)

    def contrast(angle: float) -> float:
        return _row_contrast(ink, angle)

    best = max(np.arange(-_SEARCH, _SEARCH + _COARSE / 2, _COARSE), key=contrast)
    best = max(np.arange(best - _COARSE, best + _COARSE + _FINE / 2, _FINE), key=contrast)
    angle = round(float(best), 1)
    if abs(angle) < MIN_TILT or abs(angle) > MAX_TILT:
        return 0.0
    return angle if level > 0 and _row_contrast(ink, angle) >= MIN_GAIN * level else 0.0


def _ink(image: Image) -> Image:
    """A small copy, ink white on black."""
    grey = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    scale = _WORK_WIDTH / grey.shape[1]
    if scale < 1:
        grey = cv2.resize(grey, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    _, ink = cv2.threshold(grey, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    return np.asarray(ink, dtype=np.uint8)


def _row_contrast(ink: Image, angle: float) -> float:
    """How much the ink per pixel row varies with the page turned by `angle`."""
    height, width = ink.shape
    turn = cv2.getRotationMatrix2D((width / 2, height / 2), float(angle), 1.0)
    turned = cv2.warpAffine(ink, turn, (width, height), flags=cv2.INTER_NEAREST)
    return float(np.var(turned.sum(axis=1, dtype=np.float64)))
