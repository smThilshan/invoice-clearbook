"""Charges outside the item lines (task 4.13): found by the printed amounts, offered only when
they explain the total exactly. Made-up receipts."""

from decimal import Decimal

from app.domain.enums import AmountType, DocumentType
from app.domain.invoice import Invoice, Line
from app.pipeline.read import PageText, TextSpan
from app.pipeline.verify.charges import find_charges, total_gap

HINTS = ("delivery", "service", "fee", "charge", "discount", "tip")


def page(*rows: str, source: str = "ocr") -> PageText:
    """One span per printed line, top to bottom."""
    spans = [
        TextSpan(text, (0.1, 0.05 * n, 0.9, 0.05 * n + 0.02), 0.99)
        for n, text in enumerate(rows, start=1)
    ]
    return PageText(1, 800, 1500, source, spans)  # type: ignore[arg-type]


def receipt(subtotal: str, total: str, vat: str | None = "0") -> Invoice:
    return Invoice(
        document_type=DocumentType.RECEIPT,
        lines=[Line(description="Groceries", amount=Decimal(subtotal))],
        subtotal=Decimal(subtotal),
        vat=Decimal(vat) if vat is not None else None,
        total=Decimal(total),
    )


SUMMARY = ("Order Summary", "2 x Fresh Milk 2 ltr 26.10", "Subtotal 46.86")


def labels(found: object) -> list[tuple[str, str]] | None:
    return [(c.label, str(c.amount)) for c in found] if isinstance(found, list) else None


def test_fees_between_the_subtotal_and_the_total_explain_it() -> None:
    text = page(*SUMMARY, "Delivery fee D 5.99", "Service fee 1.41", "Total 54.26", "Payment Cash")

    found = find_charges(receipt("46.86", "54.26"), [[text]], HINTS)

    assert labels(found) == [("Delivery fee", "5.99"), ("Service fee", "1.41")]


def test_a_charge_never_seen_before_is_found_by_its_place_and_amount() -> None:
    text = page(*SUMMARY, "Municipality levy AED 2.00", "Total 48.86")

    assert labels(find_charges(receipt("46.86", "48.86"), [[text]], HINTS)) == [
        ("Municipality levy", "2.00")
    ]


def test_an_amount_whose_decimal_point_was_lost_is_read_as_money() -> None:
    text = page(*SUMMARY, "Delivery fee 5.99", "Service fee 141", "Total 54.26")

    found = find_charges(receipt("46.86", "54.26"), [[text]], HINTS)

    assert labels(found) == [("Delivery fee", "5.99"), ("Service fee", "1.41")]


def test_a_discount_counts_against_the_total() -> None:
    text = page(*SUMMARY, "Delivery fee 5.99", "Promo discount 10.00", "Total 42.85")

    found = find_charges(receipt("46.86", "42.85"), [[text]], HINTS)

    assert labels(found) == [("Delivery fee", "5.99"), ("Promo discount", "-10.00")]


def test_two_possible_explanations_offer_nothing() -> None:
    text = page(*SUMMARY, "Delivery fee 2.00", "Packing charge 2.00", "Total 48.86")

    assert find_charges(receipt("46.86", "48.86"), [[text]], HINTS) is None


def test_charges_printed_elsewhere_are_found_by_their_words() -> None:
    text = page("Tip for rider 3.00", *SUMMARY, "Grand amount 49.86")  # no "Total" line

    assert labels(find_charges(receipt("46.86", "49.86"), [[text]], HINTS)) == [
        ("Tip for rider", "3.00")
    ]


def test_nothing_that_explains_the_gap_offers_nothing() -> None:
    text = page(*SUMMARY, "Delivery fee 5.99", "Total 60.00")

    assert find_charges(receipt("46.86", "60.00"), [[text]], HINTS) is None


def test_a_better_reading_is_tried_when_the_first_cannot_explain_it() -> None:
    scanner = page(*SUMMARY, "Delivery fee", "Service fee 141", "D 54.26", "Total")
    our_ocr = page(*SUMMARY, "Delivery fee 5.99", "Service fee 1.41", "Total 54.26")

    found = find_charges(receipt("46.86", "54.26"), [[scanner], [our_ocr]], HINTS)

    assert labels(found) == [("Delivery fee", "5.99"), ("Service fee", "1.41")]


def test_a_total_that_adds_up_has_no_gap() -> None:
    assert total_gap(receipt("46.86", "46.86")) is None
    assert total_gap(receipt("46.86", "49.20", vat="2.34")) is None
    assert total_gap(receipt("46.86", "54.26")) == Decimal("7.40")


def test_a_fee_left_out_of_the_lines_is_found_with_the_worked_out_subtotal() -> None:
    """Decision 81: the subtotal is the total less the VAT (110.00), so it holds the fee; the
    gap is measured from the lines."""
    read = Invoice(document_type=DocumentType.BILL, amount_type=AmountType.EXCLUSIVE,
                   lines=[Line(description="Groceries", amount=Decimal("100.00"))],
                   subtotal=Decimal("110.00"), vat=Decimal("5.50"),
                   total=Decimal("115.50"))  # fmt: skip
    text = page("Groceries 100.00", "Items 100.00", "Delivery fee 10.00", "VAT 5% 5.50",
                "Total 115.50")  # fmt: skip

    assert total_gap(read) == Decimal("10.00")
    assert labels(find_charges(read, [[text]], HINTS)) == [("Delivery fee", "10.00")]


def test_lines_including_vat_have_no_gap_when_they_make_the_total() -> None:
    read = Invoice(document_type=DocumentType.RECEIPT, amount_type=AmountType.INCLUSIVE,
                   lines=[Line(description="Bread", amount=Decimal("14.39"))],
                   subtotal=Decimal("13.70"), vat=Decimal("0.69"),
                   total=Decimal("14.39"))  # fmt: skip

    assert total_gap(read) is None
