"""Smoke test for the ExtractionClient against the real model (task 1.7).

    make smoke-llm                         # every file in data/samples/
    make smoke-llm ARGS=--show-values      # also print extracted JSON (your terminal only)
    uv run python scripts/smoke_extraction.py PATH...   # specific files or folders

Prints metrics only by default (speed, schema validity, which fields were found, whether key
values appear in the PDF text) so output can be shared without exposing invoice data.
The prompt here is a first draft; the real one is built in task 2.5.
"""

import argparse
import json
import re
import sys
import time
from contextlib import closing
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import pymupdf
from pydantic import ValidationError

from app.adapters.llm import ExtractionError, ExtractionRequest, get_extraction_client
from app.core.config import REPO_ROOT
from app.domain.invoice import Invoice
from app.pipeline.model_schema import build_model_schema

MAX_IMAGE_SIDE = 1500  # architecture §16
DEFAULT_SAMPLES = REPO_ROOT / "data" / "samples"
SUPPORTED = {".pdf", ".png", ".jpg", ".jpeg"}

SYSTEM_PROMPT = """You extract data from one supplier document into JSON that matches the schema.
Rules:
- Use only what is printed on the document. If a value is not on it, use null. Never guess.
- Dates as YYYY-MM-DD. Amounts as plain numbers: no currency symbols, no thousands separators.
- document_type: BILL = supplier tax invoice or bill; CREDIT_NOTE = supplier credit note;
  RECEIPT = proof of a payment already made (card/cash); NON_POSTABLE = quote, purchase order,
  proforma, statement, delivery note or remittance.
- amount_type: INCLUSIVE if line amounts include VAT, EXCLUSIVE if VAT is added on top, NO_TAX if
  no VAT applies.
- Leave account_code and tax_type null.
- evidence: for invoice_number, invoice_date, total, vat and vendor_trn, copy the exact text you
  read each value from."""

KEY_FIELDS = ("invoice_number", "total", "vat", "vendor_trn")


@dataclass(frozen=True)
class Page:
    png: bytes
    text: str  # PDF text layer; empty for images (OCR arrives in task 2.4)
    size: tuple[int, int]
    page_count: int


def load_first_page(path: Path) -> Page:
    with pymupdf.open(path) as doc:
        page = doc[0]
        zoom = MAX_IMAGE_SIDE / max(page.rect.width, page.rect.height)
        if not doc.is_pdf:
            zoom = min(zoom, 1.0)  # never upscale a photo
        pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
        text = page.get_text() if doc.is_pdf else ""
        return Page(pix.tobytes("png"), text, (pix.width, pix.height), doc.page_count)


def build_prompt(page: Page) -> str:
    if not page.text.strip():
        return "Extract the document in the image."
    return (
        "Extract the document in the image. Its text layer is below; prefer it for exact "
        f"characters and numbers.\n\n<document_text>\n{page.text}\n</document_text>"
    )


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


def report(label: str, page: Page, raw: str, show_values: bool) -> None:
    try:
        invoice = Invoice.model_validate_json(raw)
    except ValidationError as exc:
        # loc + type only: pydantic's error input would echo invoice values.
        problems = ", ".join(f"{'.'.join(map(str, e['loc']))}:{e['type']}" for e in exc.errors())
        print(f"  schema     : INVALID ({exc.error_count()} errors) {problems}")
        if show_values:
            print(raw)
        return

    data = invoice.model_dump()
    header = {k: v for k, v in data.items() if k not in ("lines", "evidence")}
    filled = [k for k, v in header.items() if v is not None]
    empty = [k for k, v in header.items() if v is None]
    print("  schema     : valid")
    print(f"  type       : {invoice.document_type}")
    print(f"  filled     : {', '.join(filled)}")
    print(f"  null       : {', '.join(empty) or '-'}")
    print(f"  lines      : {len(invoice.lines)}   evidence keys: {sorted(invoice.evidence)}")
    print(f"  arithmetic : {arithmetic(invoice)}")
    if page.text.strip():
        checks = [
            f"{f}={'yes' if appears_in_text(f, data[f], page.text) else 'NO'}"
            for f in KEY_FIELDS
            if data[f] is not None
        ]
        print(f"  in PDF text: {', '.join(checks) or '-'}")
    if show_values:
        print(json.dumps(invoice.model_dump(mode="json"), indent=2, ensure_ascii=False))


def collect_files(paths: list[Path]) -> list[Path]:
    files: list[Path] = []
    for path in paths:
        candidates = sorted(path.iterdir()) if path.is_dir() else [path]
        for candidate in candidates:
            if candidate.is_file() and candidate.suffix.lower() in SUPPORTED:
                files.append(candidate)
            elif not candidate.name.startswith("."):
                print(f"skipped (not a supported file): {candidate.suffix or 'no extension'}")
    return files


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("paths", nargs="*", type=Path, default=[DEFAULT_SAMPLES])
    parser.add_argument("--show-values", action="store_true", help="print extracted JSON")
    args = parser.parse_args()

    files = collect_files(args.paths)
    if not files:
        print("no supported files (.pdf .png .jpg) found", file=sys.stderr)
        return 1
    schema = build_model_schema()

    with closing(get_extraction_client()) as client:
        print(f"model: {client.model}")
        started = time.perf_counter()
        client.extract(ExtractionRequest(prompt="Reply {}", json_schema={"type": "object"}))
        print(f"warm-up (model load): {time.perf_counter() - started:.1f}s\n")

        for n, path in enumerate(files, 1):
            label = path.name if args.show_values else f"sample {n} ({path.suffix.lstrip('.')})"
            page = load_first_page(path)
            print(
                f"{label}: {page.page_count} page(s), image {page.size[0]}x{page.size[1]}, "
                f"text layer {len(page.text)} chars"
            )
            try:
                result = client.extract(
                    ExtractionRequest(
                        prompt=build_prompt(page),
                        json_schema=schema,
                        images=[page.png],
                        system_prompt=SYSTEM_PROMPT,
                    )
                )
            except ExtractionError as exc:
                print(f"  ERROR      : {exc}\n")
                continue
            print(
                f"  time       : {result.duration_ms / 1000:.1f}s   tokens in/out: "
                f"{result.prompt_tokens}/{result.output_tokens}   truncated: {result.truncated}"
            )
            report(label, page, result.raw_output, args.show_values)
            print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
