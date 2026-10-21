import json
from datetime import date
from decimal import Decimal

import pytest

from app.adapters.llm import ExtractionResult
from app.pipeline.extract import (
    EXTRACTION_SCHEMA,
    SYSTEM_PROMPT,
    build_request,
    parse_answer_dates,
    validate_answer,
)
from app.pipeline.read import PageText, TextSpan


def page(number: int, *lines: str) -> PageText:
    spans = [TextSpan(line, (0, 0, 1, 1), 0.99) for line in lines]
    return PageText(number, 1000, 1400, "ocr", spans)


def answer(raw: str, *, truncated: bool = False) -> ExtractionResult:
    return ExtractionResult("m", raw, truncated, 1, None, None)


def test_request_carries_page_text_images_schema_and_rules() -> None:
    request = build_request([page(1, "TAX INVOICE", "Total 105.00")], [b"png1"])

    assert '<page number="1">\nTAX INVOICE\nTotal 105.00\n</page>' in request.prompt
    assert list(request.images) == [b"png1"]
    assert request.json_schema is EXTRACTION_SCHEMA
    assert request.system_prompt == SYSTEM_PROMPT
    assert "Purchase" not in request.prompt  # the folder is never revealed (task 3.7)


def test_request_without_text_relies_on_images() -> None:
    request = build_request([page(1)], [b"png1"])

    assert request.prompt == "Extract the document in the image(s)."


def test_request_says_when_only_some_pages_have_images() -> None:
    request = build_request([page(1, "a"), page(2, "b"), page(3, "c")], [b"p1", b"p2"])

    assert "first 2 of 3 pages" in request.prompt


def test_extraction_schema_has_no_line_coding() -> None:
    line = EXTRACTION_SCHEMA["properties"]["lines"]["items"]["properties"]

    assert "account_code" not in line and "tax_type" not in line


def test_valid_answer() -> None:
    invoice, error = validate_answer(answer('{"document_type": "BILL", "total": 105.00}'))

    assert error is None and invoice is not None
    assert invoice.total == Decimal("105.00")


def test_invalid_answer_error_names_fields_never_values() -> None:
    raw = json.dumps({"document_type": "INVOICE", "total": "AED 1,050.10", "secret": "x"})

    invoice, error = validate_answer(answer(raw))

    assert invoice is None and error is not None
    assert error.startswith("3 schema error(s)")
    assert "document_type:enum" in error and "total:decimal_parsing" in error
    assert "1,050.10" not in error and "INVOICE" not in error.replace("document_type", "")


def test_truncated_answer_is_invalid() -> None:
    invoice, error = validate_answer(answer('{"document_type": "BI', truncated=True))

    assert invoice is None
    assert error == "answer cut off at the output token limit"


def test_non_json_answer_is_invalid() -> None:
    invoice, error = validate_answer(answer("Sorry, I cannot read this document."))

    assert invoice is None and error is not None and "json_invalid" in error


@pytest.mark.parametrize(
    ("printed", "expected"),
    [
        ("4/5/2026 (06:59)", "2026-05-04"),  # ROAST receipts: day first, time after it
        ("04/05/2026", "2026-05-04"),
        ("4-5-26", "2026-05-04"),
        ("02 Sep 2025", "2025-09-02"),
        ("Sep 2, 2025", "2025-09-02"),
        ("2026-05-04", "2026-05-04"),  # already ISO: unchanged
        ("٤/٥/٢٠٢٦", "2026-05-04"),  # Arabic-Indic digits  # noqa: RUF001
        ("Net 30", None),  # terms, not a date: missing, never guessed
        ("", None),
    ],
)
def test_printed_dates_are_read_day_first(printed: str, expected: str | None) -> None:
    assert parse_answer_dates({"invoice_date": printed})["invoice_date"] == expected


def test_answer_with_printed_dates_becomes_an_invoice() -> None:
    raw = json.dumps({"document_type": "BILL", "invoice_date": "4/5/2026", "due_date": "3/6/2026"})

    invoice, error = validate_answer(answer(raw))

    assert error is None and invoice is not None
    assert (invoice.invoice_date, invoice.due_date) == (date(2026, 5, 4), date(2026, 6, 3))


def test_money_stays_exact_after_reading_the_answer() -> None:
    invoice, _ = validate_answer(answer('{"document_type": "BILL", "total": 1050.10, '
                                        '"vat": 0.1, "lines": [{"amount": 33.33}]}'))  # fmt: skip

    assert invoice is not None
    assert (invoice.total, invoice.vat) == (Decimal("1050.10"), Decimal("0.1"))
    assert invoice.lines[0].amount == Decimal("33.33")


def test_prompt_asks_for_dates_as_printed() -> None:
    assert "Dates exactly as printed" in SYSTEM_PROMPT
    assert "YYYY-MM-DD" not in SYSTEM_PROMPT


def test_prompt_says_where_trns_are_and_what_is_not_one() -> None:
    assert "exactly 15 digits" in SYSTEM_PROMPT
    assert "VAT Registration No" in SYSTEM_PROMPT
    assert "excise" in SYSTEM_PROMPT
