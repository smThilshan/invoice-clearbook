"""Smoke test for the Read step on real samples (task 2.4): `make smoke-read`.

Runs the production code path (`read_pages`) on every file in data/samples/ and prints metrics
only: pages, text layer or OCR, how much text, OCR confidence, time. Never the text itself.
"""

import statistics
import sys
import time

from app.adapters.ocr import get_ocr_engine
from app.core.config import REPO_ROOT, get_settings
from app.pipeline import file_types
from app.pipeline.read import UnreadableError, read_pages

SAMPLES = REPO_ROOT / "data" / "samples"
LOW_CONFIDENCE = 0.8


def main() -> int:
    settings = get_settings()
    files = sorted(p for p in SAMPLES.iterdir() if p.is_file() and not p.name.startswith("."))
    if not files:
        print(f"no files in {SAMPLES}", file=sys.stderr)
        return 1

    started = time.perf_counter()
    get_ocr_engine()
    print(f"OCR models: {settings.ocr_detection_model} + {settings.ocr_recognition_model}")
    print(f"OCR load: {time.perf_counter() - started:.1f}s\n")

    for n, path in enumerate(files, 1):
        data = path.read_bytes()
        detected = file_types.detect(data[: file_types.HEAD_BYTES])
        label = f"sample {n} ({path.suffix.lstrip('.').lower()})"
        if detected is None:
            print(f"{label}: unsupported file type\n")
            continue
        started = time.perf_counter()
        try:
            pages = read_pages(
                data,
                detected.mime_type,
                get_ocr_engine,
                max_pages=settings.max_pages,
                max_side=settings.page_image_max_side,
                min_text_chars=settings.text_layer_min_chars,
            )
        except UnreadableError as exc:
            print(f"{label}: unreadable: {exc}\n")
            continue
        print(f"{label}: {len(pages)} page(s), {time.perf_counter() - started:.1f}s")
        for image, text in pages:
            scores = [s.confidence for s in text.spans if s.confidence is not None]
            line = (
                f"  page {text.page}: {text.source:<10} image {image.shape[1]}x{image.shape[0]}"
                f"  spans {len(text.spans):>3}  chars {len(text.text):>5}"
            )
            if scores:
                low = sum(score < LOW_CONFIDENCE for score in scores)
                line += (
                    f"  confidence mean {statistics.mean(scores):.3f}"
                    f" min {min(scores):.3f}  below {LOW_CONFIDENCE}: {low}"
                )
            print(line)
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
