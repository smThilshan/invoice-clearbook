"""The subtotal is worked out from the total and the VAT (decision 81). Made-up amounts."""

from decimal import Decimal

from app.domain.enums import DocumentType
from app.domain.invoice import Invoice
from app.pipeline.subtotal import with_worked_out_subtotal


def worked_out(**amounts: str | None) -> Decimal | None:
    fields = {name: Decimal(value) if value else None for name, value in amounts.items()}
    return with_worked_out_subtotal(Invoice(document_type=DocumentType.BILL, **fields)).subtotal


def test_the_subtotal_is_the_total_less_the_vat_whatever_was_read() -> None:
    """Carrefour: the model copied the total (14.39) into the subtotal."""
    assert worked_out(subtotal="14.39", vat="0.69", total="14.39") == Decimal("13.70")


def test_without_vat_the_subtotal_is_the_total() -> None:
    assert worked_out(subtotal=None, vat=None, total="20.00") == Decimal("20.00")
    assert worked_out(subtotal="18.00", vat="0", total="20.00") == Decimal("20.00")


def test_without_a_total_there_is_no_subtotal() -> None:
    """Null over invented: nothing to work it out from."""
    assert worked_out(subtotal="100.00", vat="5.00", total=None) is None


def test_a_credit_note_keeps_its_signs() -> None:
    assert worked_out(vat="-5.00", total="-105.00") == Decimal("-100.00")


def test_an_invoice_already_right_is_returned_as_it_is() -> None:
    invoice = Invoice(document_type=DocumentType.BILL, subtotal=Decimal("100.00"),
                      vat=Decimal("5.00"), total=Decimal("105.00"))  # fmt: skip

    assert with_worked_out_subtotal(invoice) is invoice
