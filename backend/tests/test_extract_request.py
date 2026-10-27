import json
from datetime import date
from decimal import Decimal

import pytest

from app.adapters.llm import ExtractionResult
from app.domain.enums import AmountType
from app.pipeline.extract import (
    EXTRACTION_SCHEMA,
    SYSTEM_PROMPT,
    build_request,
    clear_null_text,
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


def test_the_models_vat_type_guess_is_replaced_by_the_amounts() -> None:
    """Decision 34 (EXP-004): headings like "INCL EXCISE" made the model say INCLUSIVE although
    the lines plus VAT made the total."""
    raw = (
        '{"document_type": "BILL", "amount_type": "INCLUSIVE", "vat": 2.00, "total": 42.00, '
        '"lines": [{"description": "Milk", "qty": null, "unit_price": null, "amount": 40.00}]}'
    )

    invoice, _ = validate_answer(answer(raw))

    assert invoice is not None and invoice.amount_type is AmountType.EXCLUSIVE


def test_vat_type_is_worked_out_from_the_amounts() -> None:
    raw = (
        '{"document_type": "BILL", "vat": 2.00, "total": 42.00, "lines": '
        '[{"description": "Milk", "qty": null, "unit_price": null, "amount": 40.00}]}'
    )

    invoice, error = validate_answer(answer(raw))

    assert error is None and invoice is not None
    assert invoice.amount_type is AmountType.EXCLUSIVE


def test_vat_type_stays_empty_when_the_amounts_do_not_tell() -> None:
    invoice, _ = validate_answer(answer('{"document_type": "BILL", "vat": 2.00, "total": 42.00}'))

    assert invoice is not None and invoice.amount_type is None


def test_a_valid_answer_has_its_descriptions_cleaned_and_the_raw_answer_is_untouched() -> None:
    raw = ('{"document_type": "BILL", "lines": [{"description": "5 x Sugar 1kg", "qty": 5, '
           '"amount": 10}]}')  # fmt: skip
    llm = answer(raw)

    invoice, error = validate_answer(llm)

    assert error is None and invoice is not None
    assert invoice.lines[0].description == "Sugar 1kg"
    assert llm.raw_output == raw  # the model's own answer is kept as it was (decision 42)


def test_null_written_as_text_is_null() -> None:
    """Decision 57, Al Shahama: "null" as text made a customer TRN of 0 digits and an
    original-invoice reference on a bill."""
    raw = json.dumps({
        "document_type": "BILL", "invoice_number": "357", "recipient_name": "null",
        "recipient_trn": "null", "original_invoice_ref": "None", "po_number": " N/A ",
        "due_date": "null", "lines": [{"description": "null", "amount": 0.12}],
        "evidence": {"invoice_number": "357"},
    })  # fmt: skip

    invoice, error = validate_answer(answer(raw))

    assert error is None and invoice is not None
    assert invoice.invoice_number == "357"
    assert (invoice.recipient_name, invoice.recipient_trn, invoice.original_invoice_ref,
            invoice.po_number, invoice.due_date) == (None, None, None, None, None)  # fmt: skip
    assert invoice.lines[0].description is None


def test_only_the_whole_text_null_is_cleared() -> None:
    names = {"a": "Nullabor Foods", "b": "NONE LEFT", "c": 0, "d": None}

    assert clear_null_text(names) == names
