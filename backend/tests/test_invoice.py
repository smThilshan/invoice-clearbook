import json
from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.domain.enums import AmountType, DocumentType, TaxType
from app.domain.invoice import Invoice, Line

FULL_BILL = {
    "document_type": "BILL",
    "vendor_name": "  Al Noor Trading LLC ",
    "invoice_number": "INV-00291",
    "invoice_date": "2026-10-12",
    "total": 1050.10,
    "currency": "AED",
    "vendor_trn": "100123456700003",
    "recipient_name": "Missan",
    "recipient_trn": None,
    "due_date": "2026-11-11",
    "po_number": None,
    "original_invoice_ref": None,
    "amount_type": "EXCLUSIVE",
    "subtotal": 1000.10,
    "vat": 50.00,
    "lines": [
        {
            "description": "Printer paper A4",
            "qty": 10,
            "unit_price": 100.01,
            "amount": 1000.10,
            "account_code": "429",
            "tax_type": "STANDARD_5",
        }
    ],
    "evidence": {"total": "Total AED 1,050.10"},
}


def test_full_bill_parses_from_model_json() -> None:
    invoice = Invoice.model_validate_json(json.dumps(FULL_BILL))

    assert invoice.document_type is DocumentType.BILL
    assert invoice.vendor_name == "Al Noor Trading LLC"
    assert invoice.invoice_date == date(2026, 10, 12)
    assert invoice.amount_type is AmountType.EXCLUSIVE
    assert invoice.lines[0].tax_type is TaxType.STANDARD_5


def test_json_numbers_become_exact_decimals() -> None:
    invoice = Invoice.model_validate_json(json.dumps(FULL_BILL))

    assert invoice.total == Decimal("1050.10")
    assert invoice.subtotal == Decimal("1000.10")
    assert invoice.lines[0].unit_price == Decimal("100.01")
    assert isinstance(invoice.total, Decimal)


def test_only_document_type_is_required() -> None:
    invoice = Invoice.model_validate_json('{"document_type": "RECEIPT"}')

    assert invoice.invoice_number is None
    assert invoice.invoice_date is None
    assert invoice.total is None
    assert invoice.vendor_name is None
    assert invoice.lines == []
    assert invoice.evidence == {}


def test_explicit_nulls_accepted() -> None:
    nulls = {field: None for field in Invoice.model_fields if field != "document_type"}
    nulls.pop("lines")
    nulls.pop("evidence")

    invoice = Invoice.model_validate({"document_type": "BILL", **nulls})

    assert invoice.total is None


def test_missing_document_type_rejected() -> None:
    with pytest.raises(ValidationError, match="document_type"):
        Invoice.model_validate({})


def test_unknown_enum_value_rejected() -> None:
    with pytest.raises(ValidationError, match="document_type"):
        Invoice.model_validate({"document_type": "PURCHASE_ORDER"})


def test_extra_fields_rejected() -> None:
    with pytest.raises(ValidationError, match="approved"):
        Invoice.model_validate({"document_type": "BILL", "approved": True})


def test_line_amount_required() -> None:
    with pytest.raises(ValidationError, match="amount"):
        Line.model_validate({"description": "x"})


@pytest.mark.parametrize("bad", ['"NaN"', '"Infinity"', '"-Infinity"'])
def test_non_finite_money_rejected(bad: str) -> None:
    with pytest.raises(ValidationError, match="total"):
        Invoice.model_validate_json(f'{{"document_type": "BILL", "total": {bad}}}')
