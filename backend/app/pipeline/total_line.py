"""One total line (decision 86): the totals decide, the lines describe.

The item lines are where reading goes wrong (a line read twice, a summary row read as an item);
the total and the VAT are two printed amounts the checks hold to the page. When the lines do not
add up but the VAT is exactly 5% of the amount it is on, the document is sent as one line that
gives the printed total and the printed VAT to the fils, whatever the accounting system works
out from it: before VAT with VAT added on top, or else the total with VAT included. The VAT type
of the lines then does not matter, so it is not asked for.

Worked out from the invoice alone, never stored: the checks and posting call the same function,
so what the accountant approves is what is sent. Lines fixed in review add up again, and are
sent as they are. Mixed accounts or VAT types cannot be one line: those stay for the accountant.
"""

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from app.domain.enums import AmountType, TaxType
from app.domain.invoice import Invoice
from app.pipeline.verify.vat_type import vat_type_from_amounts

CENT = Decimal("0.01")
UAE_VAT_RATE = Decimal("0.05")


@dataclass(frozen=True, slots=True)
class TotalLine:
    amount_type: AmountType  # how `amount` is sent: EXCLUSIVE (before VAT) or INCLUSIVE
    amount: Decimal
    account_code: str | None  # the one account every line has


def total_line(invoice: Invoice) -> TotalLine | None:
    total, vat = invoice.total, invoice.vat
    if not invoice.lines or total is None or vat is None or vat <= 0:
        return None
    if vat_type_from_amounts(invoice) is not None:
        return None  # the lines add up: they are sent as they are
    accounts = {line.account_code for line in invoice.lines}
    if len(accounts) != 1:
        return None
    if any(line.tax_type not in (None, TaxType.STANDARD_5) for line in invoice.lines):
        return None
    (account,) = accounts
    if _vat_on(total - vat) == vat:
        return TotalLine(AmountType.EXCLUSIVE, total - vat, account)
    if _vat_in(total) == vat:
        return TotalLine(AmountType.INCLUSIVE, total, account)
    return None


def _vat_on(amount: Decimal) -> Decimal:
    return (amount * UAE_VAT_RATE).quantize(CENT, rounding=ROUND_HALF_UP)


def _vat_in(amount: Decimal) -> Decimal:
    return (amount * UAE_VAT_RATE / (1 + UAE_VAT_RATE)).quantize(CENT, rounding=ROUND_HALF_UP)
