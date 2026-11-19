"""A customer, title or label read as the supplier or the invoice number (decision 90)."""

from typing import Any

import pytest

from app.domain.enums import DocumentType
from app.domain.invoice import Invoice
from app.pipeline.header_fields import clear_false_header_fields
from app.pipeline.read import PageText, TextSpan


def bill(**fields: Any) -> Invoice:
    return Invoice(document_type=DocumentType.RECEIPT, **fields)


def page(*texts: str) -> PageText:
    spans = [TextSpan(text=t, box=(0, 0, 1, 1), confidence=0.9) for t in texts]
    return PageText(page=1, width=1000, height=1400, source="ocr", spans=spans)


PAD = page("INVOICE", "Inv. No.", "Date:", "Mr./M/s..", "IQUA<k..", "DESCRIPTION")


@pytest.mark.parametrize(
    ("vendor", "pages", "why"),
    [
        ("IQUA", [PAD], "OCR read it in the next span after Mr./M/s"),
        ("Raas", [page("M.Ms.Raas.", "DESCRIPTION")], "in the label's own span"),
        ("IS-ILL-JI", [page("Mr.", "/", "M/S..", "IS-ILL-JI")], "after M/S"),
        ("Al Fagatoura", [], "الفاتورة, the printed title"),
        ("INVOICEBSSL", [], "the printed title, Arabic read as letters"),
        ("Tax Invoice", [], "a title"),
        ("al/", [], "no three letters"),
        ("13N:100304021800003", [], "a TRN"),
    ],
)
def test_a_supplier_that_is_not_one_is_cleared(
    vendor: str, pages: list[PageText], why: str
) -> None:
    assert clear_false_header_fields(bill(vendor_name=vendor), pages).vendor_name is None, why


def test_a_supplier_that_is_the_pad_number_is_cleared() -> None:
    inv = bill(vendor_name="77217", invoice_number="77217")

    assert clear_false_header_fields(inv, []).vendor_name is None


@pytest.mark.parametrize(
    ("vendor", "pages", "why"),
    [
        ("AL SHAHAMA SUPERMARKET", [page("AL SHAHAMA SUPERMARKET", "TAX INVOICE")], "a header"),
        ("Fatima Trading", [PAD], "not after the customer label"),
        ("MSB Trading", [page("MSB Trading", "Mr. Roast")], "MSB is not M/s"),
        ("Invoice Hub Trading", [], "more than a title"),
    ],
)
def test_a_real_supplier_stays(vendor: str, pages: list[PageText], why: str) -> None:
    assert clear_false_header_fields(bill(vendor_name=vendor), pages).vendor_name == vendor, why


def test_a_supplier_also_read_as_the_customer_stays() -> None:
    """A till receipt names no customer: the model often copies the supplier there too."""
    inv = bill(vendor_name="TITI LOFT SUPERMARKET", recipient_name="TITI LOFT SUPERMARKET")

    assert clear_false_header_fields(inv, []).vendor_name == "TITI LOFT SUPERMARKET"


@pytest.mark.parametrize(("number", "kept"), [("Inv. No.", None), ("PSCIPCV", None), ("B", None),
                                              ("0034", "0034"), ("INV-12", "INV-12")])  # fmt: skip
def test_an_invoice_number_needs_a_digit(number: str, kept: str | None) -> None:
    assert clear_false_header_fields(bill(invoice_number=number), []).invoice_number == kept
