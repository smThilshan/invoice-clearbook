"""Decision 64: the totals and the items from the labelled totals block, when it proves them."""

from decimal import Decimal
from typing import Any

import pytest

from app.domain.enums import AmountType, DocumentType, TaxType
from app.domain.invoice import Invoice, Line
from app.pipeline.paid import receipt_when_paid
from app.pipeline.read import PageText, TextSpan
from app.pipeline.totals_block import apply_totals_block, read_totals_block

# Al Shahama, avocado: the item line is a fils above the VAT summary (17.50 + 0.87 = 18.37).
AVOCADO = """TAX INVOICE
AVACADO KENYA 1.00 18.38
Total Qty: 1.00 | Total Amount : 17.50
Total Tax : 0.87
Rounding: -0.12
Net Amount : 18.25
Tendered Amount 50.00
Balance Amount 31.75
5.00 17.50 0.87
Keep bill for exchange..with in 48 Hours"""

# Swades: its "Sub Total" includes the VAT.
SWADES = """VAT TRN No: 100387351800003
Samosa 15.00 2.00 30.00
Vada 5.00 2.00 10.00
Bombay Pav 10.00 0.60 6.00
Total 30.00 46.00
Sub Total 46.00
VAT 2.19
Bill Total 46.00"""


def page(text: str) -> PageText:
    rows = text.splitlines()
    spans = [TextSpan(row, (0.05, n / 40, 0.95, n / 40 + 0.02), None) for n, row in enumerate(rows)]
    return PageText(1, 1000, 2000, "ocr", spans)


def receipt(*amounts: str, **changes: Any) -> Invoice:
    fields: dict[str, Any] = {
        "document_type": DocumentType.BILL,
        "lines": [Line(description=f"item {n}", qty=Decimal(1), unit_price=Decimal(a),
                       amount=Decimal(a)) for n, a in enumerate(amounts)],
    }  # fmt: skip
    fields.update(changes)
    return Invoice(**fields)


def summary(invoice: Invoice) -> tuple[list[str], str, str, str, AmountType | None]:
    return ([str(line.amount) for line in invoice.lines], str(invoice.subtotal),
            str(invoice.vat), str(invoice.total), invoice.amount_type)  # fmt: skip


def test_copied_rows_go_the_till_fils_follows_the_vat_summary_and_rounding_is_a_line() -> None:
    """The model copied the whole block as items and took 17.50 for the subtotal."""
    copied = ("18.38", "17.50", "0.87", "-0.12", "18.25", "50.00", "31.75", "5.00", "17.50")
    fixed = apply_totals_block(receipt(*copied, total=Decimal("18.25")), [page(AVOCADO)])

    assert summary(fixed) == (["18.37", "-0.12"], "17.38", "0.87", "18.25", AmountType.INCLUSIVE)
    assert (fixed.lines[1].description, fixed.lines[1].tax_type) == ("Rounding", TaxType.NONE)
    assert fixed.lines[0].unit_price == Decimal("18.37")


def test_a_sub_total_printed_with_vat_is_not_the_amount_before_vat() -> None:
    fixed = apply_totals_block(receipt("30.00", "10.00", "6.00", subtotal=Decimal("46.00"),
                                       vat=Decimal("2.19"), total=Decimal("46.00")),
                               [page(SWADES)])  # fmt: skip

    assert summary(fixed) == (["30.00", "10.00", "6.00"], "43.81", "2.19", "46.00",
                              AmountType.INCLUSIVE)  # fmt: skip


def test_swapped_totals_and_an_item_copied_twice_with_labels_above_their_amounts() -> None:
    """Shahama 147, a tilted photo: each label lands just above its amount."""
    spans = [
        TextSpan("DOUBLE SIDED TAPE 1.00 5.50", (0.2, 0.296, 0.84, 0.31), None),
        TextSpan("Total Qty: 1.00", (0.11, 0.338, 0.39, 0.352), None),
        TextSpan("Total Amount:", (0.55, 0.360, 0.79, 0.375), None),
        TextSpan("5.24", (0.85, 0.368, 0.94, 0.380), None),
        TextSpan("Total Tax:", (0.39, 0.412, 0.58, 0.426), None),
        TextSpan("0.26", (0.64, 0.419, 0.72, 0.432), None),
        TextSpan("Net Amount: 5.50", (0.60, 0.484, 0.95, 0.498), None),
    ]
    tilted = PageText(1, 1000, 2000, "ocr", spans)
    model = receipt("5.50", "5.50", "5.50", subtotal=Decimal("5.50"), vat=Decimal("0.26"),
                    total=Decimal("5.24"))  # fmt: skip

    assert summary(apply_totals_block(model, [tilted])) == (
        ["5.50"], "5.24", "0.26", "5.50", AmountType.INCLUSIVE)  # fmt: skip


def test_vat_added_on_top_and_the_vat_line_removed() -> None:
    text = "SPK-SURFACE 60.00\nSOAP 9.00\nSub Total 69.00\nVAT 5% 3.45\nTotal 72.45"
    model = receipt("60.00", "9.00", "3.45")
    model.lines[2] = Line(description="VAT", amount=Decimal("3.45"))  # the VAT copied as a line
    fixed = apply_totals_block(model, [page(text)])

    assert summary(fixed) == (["60.00", "9.00"], "69.00", "3.45", "72.45", AmountType.EXCLUSIVE)


@pytest.mark.parametrize(
    ("text", "why"),
    [
        (AVOCADO.replace("Total Tax : 0.87", "Total Tax : 0.80"), "VAT is not 5%"),
        (AVOCADO.replace("Total Tax", "Total Saving"), "no VAT row"),
        (AVOCADO.replace("Rounding: -0.12", "Rounding: -0.50"), "a rounding above 0.24"),
        ("Milk 10.00\nNet Total 10.00\nPaid 10.00", "no VAT on the slip"),
    ],
)
def test_nothing_changes_without_proof(text: str, why: str) -> None:
    model = receipt("18.38", "17.50", total=Decimal("18.25"))

    assert apply_totals_block(model, [page(text)]) == model, why


@pytest.mark.parametrize(
    "amounts",
    [
        ("12.00", "6.00"),  # the items do not add up to the block: something is missing
        ("9.19", "9.19"),  # two items a fils off: which one?
    ],
)
def test_items_that_do_not_fit_are_left_alone(amounts: tuple[str, ...]) -> None:
    model = receipt(*amounts)

    assert apply_totals_block(model, [page(AVOCADO)]) == model


def test_a_real_last_item_priced_as_a_block_amount_is_kept() -> None:
    """Two items, the second costing 5.00 (printed in the VAT table too): both are items."""
    text = AVOCADO.replace("AVACADO KENYA 1.00 18.38", "A 13.37\nB 5.00")

    assert summary(apply_totals_block(receipt("13.37", "5.00"), [page(text)]))[0] == [
        "13.37", "5.00", "-0.12"]  # fmt: skip


def test_credit_notes_are_left_alone() -> None:
    model = receipt("18.38", document_type=DocumentType.CREDIT_NOTE)

    assert apply_totals_block(model, [page(AVOCADO)]) == model


def test_labels_name_the_rows_and_numbers_are_not_rates_or_codes() -> None:
    block = read_totals_block([page("Item 92.65\nTotal incl. VAT 92.65\nTotal VAT @ 5% 4.41")])

    assert block is not None and (block.total, block.vat) == (Decimal("92.65"), Decimal("4.41"))
    assert read_totals_block([page(SWADES.replace("VAT 2.19", ""))]) is None  # VAT TRN: no VAT


@pytest.mark.parametrize(
    ("payment", "expected"),
    [
        ("Tendered Amount 18.25\nBalance Amount 0.00", DocumentType.RECEIPT),  # exact cash
        ("Tendered Amount 18.25", DocumentType.RECEIPT),  # money given = the total
        ("Tendered Amount 0.00\nBalance Amount 18.25", DocumentType.BILL),  # misread: no proof
    ],
)
def test_a_labelled_payment_proves_a_receipt(payment: str, expected: DocumentType) -> None:
    text = AVOCADO.replace("Tendered Amount 50.00\nBalance Amount 31.75", payment)

    assert receipt_when_paid(receipt("18.37", total=Decimal("18.25")), [page(text)]).document_type \
        is expected  # fmt: skip
