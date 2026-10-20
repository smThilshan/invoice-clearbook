"""PaddleOCR 3.0.2 on PaddlePaddle 3.0.0 (decision 22).

PaddleX reads its settings from environment variables at import time, so they are set before
the import:
- PADDLE_PDX_CACHE_HOME: model files live in a known project folder (bundle it for offline
  production; models download once from Hugging Face otherwise).
- HF_HUB_OFFLINE once the models are on disk: no outbound update check at every start.
- PADDLE_PDX_LOCAL_FONT_FILE_PATH: PaddleX downloads a font from Baidu's cloud at import just
  to draw visualisations, which we never do; any local font file stops that.
The import is heavy (~20 s with model load), so engines are created once per worker process.
"""

import os
from pathlib import Path
from typing import Any

from app.adapters.ocr.base import Image, RecognizedText

_FONT_CANDIDATES = (
    "/System/Library/Fonts/Supplemental/Arial.ttf",  # macOS
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",  # Debian/Ubuntu
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",  # other Linux
)


class PaddleOcrEngine:
    def __init__(self, *, models_dir: Path, detection_model: str, recognition_model: str) -> None:
        os.environ.setdefault("PADDLE_PDX_CACHE_HOME", str(models_dir))
        downloaded = models_dir / "official_models"
        if all((downloaded / name).is_dir() for name in (detection_model, recognition_model)):
            # Models on disk: no update check against Hugging Face at every start.
            os.environ.setdefault("HF_HUB_OFFLINE", "1")
        font = next((path for path in _FONT_CANDIDATES if Path(path).is_file()), None)
        if font:
            os.environ.setdefault("PADDLE_PDX_LOCAL_FONT_FILE_PATH", font)

        from paddleocr import PaddleOCR  # type: ignore[import-untyped]  # heavy, workers only

        self._ocr: Any = PaddleOCR(
            text_detection_model_name=detection_model,
            text_recognition_model_name=recognition_model,
            # Off: page rotation is fixed before OCR, and these add seconds per page on CPU.
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
        )

    def recognize(self, image: Image) -> list[RecognizedText]:
        [result] = self._ocr.predict(image)
        return [
            RecognizedText(
                text=str(text),
                confidence=float(score),
                box=(float(box[0]), float(box[1]), float(box[2]), float(box[3])),
            )
            for text, score, box in zip(
                result["rec_texts"], result["rec_scores"], result["rec_boxes"], strict=True
            )
        ]
