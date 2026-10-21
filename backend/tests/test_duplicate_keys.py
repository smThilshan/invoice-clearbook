from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from app.domain.enums import DocumentType
from app.domain.invoice import Invoice
from app.pipeline.verify.duplicates import business_key, normalize_vendor_name, vendor_key


def invoice(**fields: Any) -> Invoice:
    return Invoice(document_type=DocumentType.BILL, **fields)


@pytest.mark.parametrize(
    "printed",
    ["Al Waha Hypermarket LLC", "AL WAHA HYPERMARKET L.L.C.", "Al-Waha Hypermarket, L L C",
     "al waha  hypermarket", "Al Waha Hypermarket Co. LLC", "Al Waha Hypermarket Est."],
)  # fmt: skip
def test_supplier_name_spellings_normalize_alike(printed: str) -> None:
    assert normalize_vendor_name(printed) == "al waha hypermarket"


@pytest.mark.parametrize(
    ("printed", "expected"),
    [
        ("Gulf Catch Seafood FZ-LLC", "gulf catch seafood"),
        ("Bright Clean & Co. LLC", "bright clean"),
        ("Black & White Cafe", "black and white cafe"),  # "and" inside a name stays
        ("Coco Mart", "coco mart"),  # "co" inside a word is not a legal form
        ("LLC", None),
        ("", None),
        (None, None),
    ],
)
def test_supplier_name_edge_cases(printed: str | None, expected: str | None) -> None:
    assert normalize_vendor_name(printed) == expected


def test_vendor_key_prefers_a_valid_trn_over_the_name() -> None:
    by_trn = invoice(vendor_name="Al Waha LLC", vendor_trn="100-9990-0000-0001")
    renamed = invoice(vendor_name="AL WAHA HYPERMARKET", vendor_trn="100999000000001")

    assert vendor_key(by_trn) == vendor_key(renamed) == "TRN:100999000000001"


def test_vendor_key_falls_back_to_the_name() -> None:
    assert vendor_key(invoice(vendor_name="Corner Grocery", vendor_trn="12345")) == (
        "NAME:corner grocery"
    )
    assert vendor_key(invoice()) is None


@pytest.mark.parametrize("printed", ["INV-00291", "inv 291", "#291", "Invoice No: 291"])
def test_business_key_uses_the_normalized_number(printed: str) -> None:
    assert business_key(invoice(invoice_number=printed)) == "N:291"


def test_business_key_falls_back_to_date_and_total_for_receipts() -> None:
    receipt = invoice(invoice_date=date(2026, 10, 15), total=Decimal("235.5"))

    assert business_key(receipt) == "F:2026-10-15|235.50"


def test_fallback_key_is_the_same_for_a_credit_note_printed_negative() -> None:
    plus = invoice(invoice_date=date(2026, 10, 15), total=Decimal("50.00"))
    minus = invoice(invoice_date=date(2026, 10, 15), total=Decimal("-50.00"))

    assert business_key(plus) == business_key(minus)


def test_no_business_key_without_number_or_date_and_total() -> None:
    assert business_key(invoice(total=Decimal("10.00"))) is None
    assert business_key(invoice(invoice_number="#")) is None
