import json
from decimal import Decimal
from typing import Any

from app.domain.enums import AmountType, DocumentType
from app.domain.invoice import Invoice, Line
from app.pipeline.no_vat import clear_unprinted_vat, cleared_vat
from app.pipeline.read import PageText, TextSpan

TALABAT = """Order Details
talabat mart
Order ID: 3526727111
Subtotal D 98.87
Delivery fee D 0.00
Service fee D 2.97
Total D 101.84
Payment method Cash"""


def pages(text: str) -> list[PageText]:
    rows = text.splitlines()
    spans = [TextSpan(row, (0.0, i / len(rows), 1.0, (i + 0.5) / len(rows)), None)
             for i, row in enumerate(rows)]  # fmt: skip
    return [PageText(1, 1000, 1400, "ocr", spans)]


def order(**changes: Any) -> Invoice:
    fields: dict[str, Any] = {
        "document_type": DocumentType.RECEIPT,
        "vendor_name": "talabat mart",
        "amount_type": AmountType.INCLUSIVE,
        "subtotal": Decimal("98.87"),
        "vat": Decimal("2.97"),
        "total": Decimal("101.84"),
        "lines": [Line(description="Eggs", amount=Decimal("98.87")),
                  Line(description="Service fee", amount=Decimal("2.97"))],
    }  # fmt: skip
    fields.update(changes)
    return Invoice(**fields)


def test_vat_read_on_an_app_screen_is_cleared_and_the_vat_type_is_no_vat() -> None:
    cleared = clear_unprinted_vat(order(), pages(TALABAT))

    assert (cleared.vat, cleared.amount_type) == (None, AmountType.NO_TAX)
    assert [line.amount for line in cleared.lines] == [Decimal("98.87"), Decimal("2.97")]
    assert cleared.total == Decimal("101.84")  # amounts are never touched


def test_a_receipt_that_prints_vat_keeps_it() -> None:
    """Porter / Careem trips that show "VAT AED 1.08"."""
    printed = TALABAT.replace("Service fee D 2.97", "VAT D 2.97")

    assert clear_unprinted_vat(order(), pages(printed)) == order()


def test_a_document_with_a_supplier_trn_keeps_its_vat() -> None:
    """A tax invoice whose VAT word the OCR lost (decision 47)."""
    taxed = order(vendor_trn="100275510400003")

    assert clear_unprinted_vat(taxed, pages(TALABAT)) == taxed


def test_no_vat_read_means_nothing_to_clear() -> None:
    nothing = order(vat=None, amount_type=AmountType.NO_TAX)

    assert clear_unprinted_vat(nothing, pages(TALABAT)) == nothing


def test_the_review_screen_is_told_what_the_model_read() -> None:
    raw = json.dumps({"vat": 2.97, "total": 101.84})

    assert cleared_vat(raw, {"vat": None}) == "2.97"
    assert cleared_vat(raw, {"vat": "2.97"}) is None  # kept: nothing was cleared
    assert cleared_vat(json.dumps({"vat": None}), {"vat": None}) is None
    assert cleared_vat("not json", {"vat": None}) is None
