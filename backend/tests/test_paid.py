from decimal import Decimal
from typing import Any

import pytest

from app.domain.enums import DocumentType
from app.domain.invoice import Invoice
from app.pipeline.paid import receipt_when_paid
from app.pipeline.read import PageText, TextSpan

# Al Shahama, as our OCR read it: titled TAX INVOICE, paid in cash at the till.
SHAHAMA = """TAX INVOICE
CUSTOMER COPY
PRAN DRY CAKE RUSK 100 GM 1.00 3.00
OMAN CHIPS -15 GRM 2.00 1.50
Total Qty: 3.00 Total Amount : 4.28
Total Tax : 0.22
Net Amount : 4.50
Tendered Amount 5.00
Balance Amount 0.50
Vat % Taxable Amount Vat
5.00 4.28 0.22"""


def pages(text: str) -> list[PageText]:
    spans = [TextSpan(row, (0.0, 0.0, 1.0, 1.0), None) for row in text.splitlines()]
    return [PageText(1, 1000, 1400, "ocr", spans)]


def bill(**changes: Any) -> Invoice:
    fields: dict[str, Any] = {"document_type": DocumentType.BILL, "total": Decimal("4.50")}
    fields.update(changes)
    return Invoice(**fields)


def test_cash_given_less_the_total_printed_as_the_change_makes_it_a_receipt() -> None:
    """5.00 given - 4.50 = 0.50 change, all printed after the total."""
    assert receipt_when_paid(bill(), pages(SHAHAMA)).document_type is DocumentType.RECEIPT


def test_ocr_digit_misreads_are_read_as_digits() -> None:
    text = SHAHAMA.replace("Tendered Amount 5.00", "Tendered Amount S.OO")

    assert receipt_when_paid(bill(), pages(text)).document_type is DocumentType.RECEIPT


@pytest.mark.parametrize(
    ("text", "why"),
    [
        (
            SHAHAMA.replace("5.00\nBalance Amount 0.50", "4.50\nBalance Amount 0.00"),
            "exact cash: 0.00 change proves nothing by the numbers",
        ),
        (
            SHAHAMA.replace("Balance Amount 0.50", "Balance Amount 0.60"),
            "the change does not match",
        ),
        (
            SHAHAMA.replace("Tendered", "Loyalty").replace("Balance", "Points"),
            "no payment word: the numbers alone may be anything",
        ),
        (
            "TAX INVOICE Cash/Credit\nItem A 5.00\nItem B 0.50\nTotal 4.50",
            "the numbers that fit come before the total: items, not the payment",
        ),
        (
            SHAHAMA.replace("Net Amount : 4.50", "Net Amount : 4.40"),
            "the total read is not printed",
        ),
    ],
)
def test_nothing_changes_without_proof(text: str, why: str) -> None:
    assert receipt_when_paid(bill(), pages(text)).document_type is DocumentType.BILL, why


@pytest.mark.parametrize(
    "invoice",
    [
        bill(total=None),
        bill(document_type=DocumentType.CREDIT_NOTE),
        bill(document_type=DocumentType.NON_POSTABLE),
    ],
)
def test_only_a_bill_with_a_total_is_changed(invoice: Invoice) -> None:
    assert receipt_when_paid(invoice, pages(SHAHAMA)) == invoice
