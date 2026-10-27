"""No VAT on a document that is not a tax invoice (decision 52): app and shop screens (Talabat,
Careem, Noon...) print no supplier TRN and no VAT, so no VAT can be on them or be reclaimed.
The model still often fills VAT from a fee ("Service fee 2.97"). Right after extraction, such a
VAT is cleared and the VAT type set again from the amounts (decision 34): No VAT. Coding then
gives every line the no-VAT code (an unregistered supplier).

Same rule as the check that flags it (decision 47): no supplier TRN and no VAT word anywhere,
so a receipt that does show VAT is left alone. Never hidden: the model's own answer stays in
`extractions.raw_output` and the review screen shows what it read.
"""

import json
from typing import Any

from app.domain.invoice import Invoice
from app.pipeline.read import PageText
from app.pipeline.verify.grounding import not_a_tax_invoice
from app.pipeline.verify.vat_type import vat_type_from_amounts


def clear_unprinted_vat(invoice: Invoice, pages: list[PageText]) -> Invoice:
    if not invoice.vat or not not_a_tax_invoice(invoice, pages):
        return invoice
    cleared = invoice.model_copy(update={"vat": None})
    return cleared.model_copy(update={"amount_type": vat_type_from_amounts(cleared)})


def cleared_vat(raw_output: str, data: dict[str, Any] | None) -> str | None:
    """The VAT the model read when the stored extraction cleared it, for the review screen."""
    try:
        answer = json.loads(raw_output)
    except ValueError:
        return None
    if not isinstance(answer, dict) or data is None or data.get("vat") is not None:
        return None
    read = answer.get("vat")
    return str(read) if read not in (None, 0, "0", "") else None
