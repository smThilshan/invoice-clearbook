"""Smoke test for Read + Extract on real samples (tasks 1.7, 2.5).

    make smoke-llm                         # every file in data/samples/
    make smoke-llm ARGS=--show-values      # also print extracted JSON (your terminal only)
    uv run python scripts/smoke_extraction.py PATH...   # specific files or folders

Runs the production path: `read_pages` (text layer / OCR) -> `build_request` (page text +
images) -> model -> `validate_answer`. Prints metrics only by default (speed, schema validity,
which fields were found, arithmetic, whether key values appear in the page text), so the output
can be shared without exposing invoice data.
"""

import argparse
import json
import re
import sys
import time
from contextlib import closing
from decimal import Decimal
from pathlib import Path

from app.adapters.llm import ExtractionError, get_extraction_client
from app.adapters.ocr import get_ocr_engine
from app.core.config import REPO_ROOT, get_settings
from app.domain.invoice import Invoice
from app.pipeline import file_types
from app.pipeline.extract import build_request, validate_answer
from app.pipeline.read import UnreadableError, encode_png, read_pages

DEFAULT_SAMPLES = REPO_ROOT / "data" / "samples"
KEY_FIELDS = ("invoice_number", "total", "vat", "vendor_trn")


def _digits(value: str) -> str:
    return re.sub(r"\D", "", value)


def _squash(value: str) -> str:
    return re.sub(r"\W", "", value).lower()


def appears_in_text(field: str, value: object, text: str) -> bool:
    """Rough grounding preview (the real check is task 3.3)."""
    if field == "invoice_number":
        wanted, haystack = _squash(str(value)), _squash(text)
    else:
        wanted = _digits(f"{value:.2f}" if isinstance(value, Decimal) else str(value))
        haystack = _digits(text)
    return bool(wanted) and wanted in haystack


def arithmetic(invoice: Invoice) -> str:
    """Value-free accuracy signal: do the extracted numbers agree with each other (±0.01)?"""

    def agrees(a: Decimal, b: Decimal) -> str:
        return "ok" if abs(a - b) <= Decimal("0.01") else "MISMATCH"

    subtotal, vat, total = invoice.subtotal, invoice.vat, invoice.total
    checks: list[str] = []
    if subtotal is not None and vat is not None and total is not None:
        checks.append(f"subtotal+vat=total {agrees(subtotal + vat, total)}")
    if invoice.lines:
        line_sum = sum((line.amount for line in invoice.lines), Decimal(0))
        if subtotal is not None:
            checks.append(f"lines=subtotal {agrees(line_sum, subtotal)}")
        elif total is not None:
            checks.append(f"lines=total {agrees(line_sum, total)}")
    return ", ".join(checks) or "not enough values to check"


def report(invoice: Invoice, page_text: str, show_values: bool) -> None:
    data = invoice.model_dump()
    header = {k: v for k, v in data.items() if k not in ("lines", "evidence")}
    print("  schema     : valid")
    print(f"  type       : {invoice.document_type}")
    print(f"  filled     : {', '.join(k for k, v in header.items() if v is not None)}")
    print(f"  null       : {', '.join(k for k, v in header.items() if v is None) or '-'}")
    print(f"  lines      : {len(invoice.lines)}   evidence keys: {sorted(invoice.evidence)}")
    print(f"  arithmetic : {arithmetic(invoice)}")
    checks = [
        f"{f}={'yes' if appears_in_text(f, data[f], page_text) else 'NO'}"
        for f in KEY_FIELDS
        if data[f] is not None
    ]
    print(f"  in text    : {', '.join(checks) or '-'}")
    if show_values:
        print(json.dumps(invoice.model_dump(mode="json"), indent=2, ensure_ascii=False))


def collect_files(paths: list[Path]) -> list[Path]:
    files: list[Path] = []
    for path in paths:
        candidates = sorted(path.iterdir()) if path.is_dir() else [path]
        files += [c for c in candidates if c.is_file() and not c.name.startswith(".")]
    return files


def seconds(ms: int | None) -> str:
    return f"{(ms or 0) / 1000:.1f}s"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("paths", nargs="*", type=Path, default=[DEFAULT_SAMPLES])
    parser.add_argument("--show-values", action="store_true", help="print extracted JSON")
    args = parser.parse_args()
    settings = get_settings()

    files = collect_files(args.paths)
    if not files:
        print("no files found", file=sys.stderr)
        return 1

    with closing(get_extraction_client(settings)) as client:
        print(f"model: {client.model}   OCR: {settings.ocr_detection_model}\n")
        for n, path in enumerate(files, 1):
            label = path.name if args.show_values else f"sample {n} ({path.suffix.lstrip('.')})"
            data = path.read_bytes()
            detected = file_types.detect(data[: file_types.HEAD_BYTES])
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
            texts = [text for _, text in pages]
            images = [encode_png(image) for image, _ in pages[: settings.extract_max_images]]
            sources = ",".join(sorted({t.source for t in texts}))
            read_time = time.perf_counter() - started
            print(f"{label}: {len(pages)} page(s) via {sources}, read {read_time:.1f}s")
            try:
                llm = client.extract(build_request(texts, images))
            except ExtractionError as exc:
                print(f"  ERROR      : {exc}\n")
                continue
            print(
                f"  model      : {seconds(llm.duration_ms)} (reading {seconds(llm.reading_ms)},"
                f" writing {seconds(llm.writing_ms)})"
                f"  tokens in/out {llm.prompt_tokens}/{llm.output_tokens}"
            )
            invoice, error = validate_answer(llm)
            if invoice is None:
                print(f"  schema     : INVALID {error}")
                if args.show_values:
                    print(llm.raw_output)
            else:
                report(invoice, "\n".join(t.text for t in texts), args.show_values)
            print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
