"""VAT type from the amounts (decision 34): whether the line amounts include VAT.

The model is not asked: it judged from headings and got misled ("INCL EXCISE" read as
including VAT, EXP-004), although the amounts it read were right. The amounts answer it:

    VAT > 0 and lines + VAT = total   -> EXCLUSIVE (VAT added on top)
    VAT > 0 and lines = total         -> INCLUSIVE (VAT included)
    no VAT and lines = total          -> NO_TAX
    anything else                     -> None: the accountant chooses (never guessed, rule 9)

"Anything else" covers no lines, a missing total, discounts or charges outside the lines, and
misread amounts; the checks flag those separately.
"""

from decimal import Decimal

from app.domain.enums import AmountType
from app.domain.invoice import Invoice

CENT = Decimal("0.01")


def vat_type_from_amounts(invoice: Invoice) -> AmountType | None:
    total = invoice.total
    if not invoice.lines or total is None:
        return None
    lines = sum((line.amount for line in invoice.lines), Decimal(0))
    vat = invoice.vat or Decimal(0)
    if vat == 0:
        return AmountType.NO_TAX if _same(lines, total) else None
    if _same(lines + vat, total):
        return AmountType.EXCLUSIVE
    if _same(lines, total):
        return AmountType.INCLUSIVE
    return None


def _same(a: Decimal, b: Decimal) -> bool:
    return abs(a - b) <= CENT
