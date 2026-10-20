"""OCR port: page image in, recognised text spans with positions out.

The engine is swappable (PaddleOCR now; RapidOCR or others later) without touching the pipeline.
"""

from dataclasses import dataclass
from typing import Protocol

import numpy as np
import numpy.typing as npt

Image = npt.NDArray[np.uint8]  # H x W x 3, BGR (OpenCV convention)


@dataclass(frozen=True, slots=True)
class RecognizedText:
    text: str
    confidence: float
    box: tuple[float, float, float, float]  # x0, y0, x1, y1 in image pixels


class OcrEngine(Protocol):
    def recognize(self, image: Image) -> list[RecognizedText]: ...
