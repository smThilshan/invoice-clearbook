"""Paid on the spot (decision 61), proven by the printed numbers: one of the proofs of payment
that make a document an expense (decision 88, `pipeline/payment.py`).

Proven means all of these:
- A total was read, and the page shows a payment word (the type check's own: cash, tendered,
  paid, change, card ...).
- After the total, the page prints the cash given and the change, with cash given - total =
  change exactly ("Net Amount 4.50 / Tendered 5.00 / Balance 0.50"). Change is only given on
  money paid. Found by their numbers only: OCR garbles the labels, not the numbers.

Or, from the labelled totals block (decision 64), whose total is the one read: an amount
labelled as money given (Tendered, Cash, Card, Paid) equal to the total ("Tendered 7.00"), or
above it with one labelled as change (Balance, Change) equal to the difference, 0.00 included
("Tendered 3.50 / Balance 0.00": exact cash): weaker, as account suppliers print the total as
money given too ("PAYMENT DETAILS CASH: 49.35", Al Ain).
"""

from collections.abc import Sequence
from decimal import Decimal
from enum import StrEnum

from app.domain.invoice import Invoice
from app.pipeline.printed_summary import MONEY
from app.pipeline.read import PageText
from app.pipeline.totals_block import read_totals_block
from app.pipeline.verify.normalize import fold_ocr_digits, to_ascii_digits
from app.pipeline.verify.type_check import shows_payment


class Proof(StrEnum):
    CHANGE = "cash given and change printed"
    LABEL = "money given printed"


def proven_payment(invoice: Invoice, pages: Sequence[PageText]) -> Proof | None:
    total = invoice.total
    if not total or total < 0 or not shows_payment(pages):
        return None
    if _change_given(total, _money_in_order(pages)):
        return Proof.CHANGE
    return Proof.LABEL if _paid_by_label(total, pages) else None


def _paid_by_label(total: Decimal, pages: Sequence[PageText]) -> bool:
    block = read_totals_block(pages)
    return (
        block is not None
        and not block.from_cash  # cash less change made that total: no proof of a payment
        and block.total == total
        and any(given == total or given - total in block.change for given in block.paid)
    )


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
