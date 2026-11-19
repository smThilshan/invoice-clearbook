"""The total copied as a line (decision 90): on a handwritten pad the total is written under the
items, in the Amount column, and the model copies it as a last line "Total" 59, often leaving
the total itself empty. That line books the whole bill a second time.

It goes when the model's own numbers agree that it is the total (rule 7: arithmetic and the
model's own answer, nothing asked): the last line is named only total (or grand / net total,
المجموع) and its amount is the total read, or, with no total read, the one number in the text
copied as the total's evidence. The total is then that amount. Otherwise nothing changes and
the checks flag it as before. The model's own answer stays in `extractions.raw_output`.
"""

import re

from app.domain.invoice import Invoice
from app.pipeline.vat_line import evidence_total
from app.pipeline.verify.vat_type import vat_type_from_amounts

_TOTAL_ROW = re.compile(
    r"^\W*((grand|net)\s+)?total(\s+amount)?\W*$|^\W*المجموع\W*$", re.IGNORECASE
)


def remove_total_row(invoice: Invoice) -> Invoice:
    if len(invoice.lines) < 2:
        return invoice
    *items, last = invoice.lines
    if not _TOTAL_ROW.match(last.description or ""):
        return invoice
    total = invoice.total if invoice.total is not None else evidence_total(invoice)
    if last.amount != total:
        return invoice
    fixed = invoice.model_copy(update={"lines": items, "total": total})
    return fixed.model_copy(update={"amount_type": vat_type_from_amounts(fixed)})
