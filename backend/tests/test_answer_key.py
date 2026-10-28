"""Answer key comparison (decision 63): what counts as reading a document right."""

from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from app.domain.enums import AmountType, DocumentType
from app.domain.invoice import Invoice, Line
from app.services.answer_key import FIELDS, differences


def slip(**changes: Any) -> Invoice:
    """Al Shahama 387 as the accountant corrected it."""
    fields: dict[str, Any] = {
        "document_type": DocumentType.RECEIPT,
        "vendor_name": "AL SHAHAMA SUPERMARKET",
        "vendor_trn": "100304657800003",
        "invoice_number": "387",
        "invoice_date": date(2025, 8, 21),
        "subtotal": Decimal("2.38"),
        "vat": Decimal("0.12"),
        "total": Decimal("2.50"),
        "amount_type": AmountType.INCLUSIVE,
        "lines": [Line(description="BANNANA PHILLIPINE", amount=Decimal("2.59")),
                  Line(description="Rounding", amount=Decimal("-0.09"))],
    }  # fmt: skip
    fields.update(changes)
    return Invoice(**fields)


def test_the_same_answer_has_no_differences() -> None:
    assert differences(slip(), slip()) == {}


def test_values_compare_as_values_and_names_without_punctuation() -> None:
    read = slip(total=Decimal("2.5"), vendor_name="Al Shahama.Supermarket", invoice_number="No 387",
                lines=[Line(description="Rounding", amount=Decimal("-0.090")),
                       Line(description="Banana", amount=Decimal("2.590"))])  # fmt: skip

    assert set(differences(slip(), read)) == {"invoice_number"}  # "NO387" is not "387"


def test_codes_are_not_compared() -> None:
    """Account and VAT codes come from coding and the accountant, not the reading."""
    coded = slip(lines=[Line(description="Banana", amount=Decimal("2.59"), account_code="310"),
                        Line(description="Rounding", amount=Decimal("-0.09"))])  # fmt: skip

    assert differences(slip(), coded) == {}


@pytest.mark.parametrize(
    ("changes", "field", "shown"),
    [
        ({"document_type": DocumentType.BILL}, "document_type", ("RECEIPT", "BILL")),
        ({"amount_type": None}, "amount_type", ("INCLUSIVE", "empty")),
        ({"subtotal": Decimal("2.47")}, "subtotal", ("2.38", "2.47")),
        ({"lines": [Line(description="Banana", amount=Decimal("2.59")),
                    Line(description=None, amount=Decimal("2.59"))]},
         "lines", ("2.59 + -0.09", "2.59 + 2.59")),
    ],
)  # fmt: skip
def test_a_wrong_field_shows_expected_and_read(changes: Any, field: str, shown: Any) -> None:
    assert differences(slip(), slip(**changes)) == {field: shown}


def test_no_answer_read_is_wrong_everywhere() -> None:
    assert set(differences(slip(), None)) == set(FIELDS)
