"""The subtotal is worked out, never read (decision 81): it is the total less the VAT, so a copy
read off the page could only disagree with the two amounts that matter. The model still
answers it (its prompt and schema unchanged) and that answer stays in `extractions.raw_output`;
the working copy holds the worked-out value, set after extraction and at every check.

No total: no subtotal (null over invented). No VAT: the subtotal is the total.
"""

from decimal import Decimal

from app.domain.invoice import Invoice


def worked_out_subtotal(invoice: Invoice) -> Decimal | None:
    if invoice.total is None:
        return None
    return invoice.total - (invoice.vat or Decimal(0))


def with_worked_out_subtotal(invoice: Invoice) -> Invoice:
    subtotal = worked_out_subtotal(invoice)
    if invoice.subtotal == subtotal:
        return invoice
    return invoice.model_copy(update={"subtotal": subtotal})
