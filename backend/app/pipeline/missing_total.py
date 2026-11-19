"""A total the model left empty, though it quoted it (decision 96).

CRS/17933 (a faint scan): the model answered "total": null but copied "120.75" as the total's
evidence, with subtotal 115.00, VAT 5.75 and lines adding up to 115.00. The total is that
quoted amount when the model's own numbers prove it (rule 7: nothing is guessed):
- the evidence holds exactly one amount;
- the subtotal plus the VAT is that amount (a zero VAT counts: no VAT);
- the lines add up to the subtotal.
Otherwise the total stays empty and the checks flag it as before.

The VAT type is then set from the amounts, as when the VAT line goes (decision 60). The model's
own answer stays in `extractions.raw_output`, and the checks say where the total came from
(`step._total_from_evidence`).
"""

from decimal import Decimal

from app.domain.invoice import Invoice
from app.pipeline.verify.normalize import find_amounts
from app.pipeline.verify.vat_type import vat_type_from_amounts

CENT = Decimal("0.01")


def fill_total_from_evidence(invoice: Invoice) -> Invoice:
    total = quoted_total(invoice)
    subtotal, vat = invoice.subtotal, invoice.vat
    if invoice.total is not None or total is None or subtotal is None or vat is None:
        return invoice
    lines = sum((line.amount for line in invoice.lines), Decimal(0))
    if not invoice.lines or abs(subtotal + vat - total) > CENT or abs(lines - subtotal) > CENT:
        return invoice
    filled = invoice.model_copy(update={"total": total})
    return filled.model_copy(update={"amount_type": vat_type_from_amounts(filled)
                                     or invoice.amount_type})  # fmt: skip


def quoted_total(invoice: Invoice) -> Decimal | None:
    """The one amount in the text the model copied as the total's evidence."""
    amounts = find_amounts((invoice.evidence or {}).get("total", ""))
    return amounts.pop() if len(amounts) == 1 else None
