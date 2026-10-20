"""OCR engines behind one port (PaddleOCR now)."""

from functools import lru_cache

from app.adapters.ocr.base import Image, OcrEngine, RecognizedText
from app.core.config import get_settings

__all__ = ["Image", "OcrEngine", "RecognizedText", "get_ocr_engine"]


@lru_cache
def get_ocr_engine() -> OcrEngine:
    """One engine per process: loading the models takes ~20 s on CPU."""
    from app.adapters.ocr.paddle import PaddleOcrEngine

    settings = get_settings()
    return PaddleOcrEngine(
        models_dir=settings.ocr_models_dir,
        detection_model=settings.ocr_detection_model,
        recognition_model=settings.ocr_recognition_model,
    )
