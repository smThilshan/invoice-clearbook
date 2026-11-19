"""A barcode row read as an item (decision 90): a till prints each item on two rows, the name
with its quantity and amount, then the barcode with "1 @ 3.50", the price of one. The model
sometimes reads that second row as another item, "6297000259048 1" 3.50.

Such a row goes when the arithmetic proves it (rule 7): it holds only a barcode (8 to 14
digits) and a quantity or "@" price, its amount is the unit price of the line above, and
without these rows the lines add up to the total, where with them they do not. The barcode
itself is attached to its item later, from the page (decision 82). Otherwise nothing changes.
"""

import re

from app.domain.invoice import Invoice
from app.pipeline.verify.vat_type import vat_type_from_amounts

_BARCODE_ROW = re.compile(r"^\s*\d{8,14}(\s+\d+(\s*@\s*[\d.]*)?)?\s*$")


def remove_barcode_rows(invoice: Invoice) -> Invoice:
    lines = invoice.lines
    if vat_type_from_amounts(invoice) is not None:
        return invoice  # the lines add up: nothing to prove
    kept = [line for line, above in zip(lines, [None, *lines], strict=False)
            if not (above is not None and _BARCODE_ROW.match(line.description or "")
                    and line.amount == above.unit_price)]  # fmt: skip
    if len(kept) == len(lines):
        return invoice
    amount_type = vat_type_from_amounts(invoice.model_copy(update={"lines": kept}))
    if amount_type is None:
        return invoice
    return invoice.model_copy(update={"lines": kept, "amount_type": amount_type})
