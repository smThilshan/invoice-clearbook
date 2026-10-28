"""Paid on the spot (decision 61): a shop slip titled "TAX INVOICE" is read as a bill (the prompt:
BILL = supplier tax invoice), although it was paid in cash at the till. When the page proves the
payment, right after extraction, the type becomes RECEIPT: posted the same way (decision 50), due
on its own date.

Proven means all of these (otherwise nothing changes and the type check asks, with one click):
- The model read a bill with a total, and the page shows a payment word (the type check's own:
  cash, tendered, paid, change, card ...).
- After the total, the page prints the cash given and the change, with cash given - total =
  change exactly ("Net Amount 4.50 / Tendered 5.00 / Balance 0.50"). Change is only given on
  money paid. Found by their numbers only: OCR garbles the labels, not the numbers.

Exact cash (change 0.00) and card payments are not proven by the numbers: the type check asks.
The model's own answer stays in `extractions.raw_output`.
"""

from collections.abc import Sequence
from decimal import Decimal

from app.domain.enums import DocumentType
from app.domain.invoice import Invoice
from app.pipeline.printed_summary import MONEY
from app.pipeline.read import PageText
from app.pipeline.verify.normalize import fold_ocr_digits, to_ascii_digits
from app.pipeline.verify.type_check import shows_payment


def receipt_when_paid(invoice: Invoice, pages: Sequence[PageText]) -> Invoice:
    total = invoice.total
    if invoice.document_type is not DocumentType.BILL or not total or total < 0:
        return invoice
    if not shows_payment(pages) or not _change_given(total, _money_in_order(pages)):
        return invoice
    return invoice.model_copy(update={"document_type": DocumentType.RECEIPT})


def _money_in_order(pages: Sequence[PageText]) -> list[Decimal]:
    """Amounts as printed, in reading order (OCR digit confusions folded on scans)."""
    texts = (fold_ocr_digits(page.text) if page.source == "ocr" else page.text for page in pages)
    found = MONEY.findall(to_ascii_digits("\n".join(texts)))
    return [Decimal(text.replace(",", "")) for text in found]


def _change_given(total: Decimal, amounts: list[Decimal]) -> bool:
    """Cash given and its change, both printed after the total (the items come before it)."""
    if total not in amounts:
        return False
    after = amounts[amounts.index(total) + 1 :]
    return any(given > total and given - total in after for given in after)
