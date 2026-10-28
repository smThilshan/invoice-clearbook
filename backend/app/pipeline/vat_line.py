"""VAT copied as a line (decision 60): the model reads the VAT and also copies its printed row as
an item ("Vat @ 5%" 3.75). In Xero VAT is never a line: each line carries a tax rate and Xero
works the VAT out, so such a line books the VAT as a cost and counts it twice.

Decision 56 removes these rows when the page text proves the summary. A handwritten receipt has
no such proof: OCR does not read handwriting. The model's own numbers can still prove the answer
by arithmetic alone (rule 7: the page is not asked, nothing is read from it). The line goes when
all of these hold (otherwise nothing changes and the checks flag it as before):
- Exactly one line names VAT or tax (not excise, tourism or municipality fees) and its amount
  is the VAT the model read.
- The total is the model's, or, when it left it empty, the one number in the text it copied as
  the total's evidence.
- The other lines are either the amount before VAT (VAT added on top: lines + VAT = total) or
  the total (VAT included), exactly one of the two, and the VAT is 5% of the amount before VAT.

The subtotal becomes the total less the VAT, and the VAT type is set from the amounts. The
model's own answer stays in `extractions.raw_output`.
"""

import re
from decimal import Decimal

from app.domain.invoice import Invoice
from app.pipeline.verify.normalize import find_amounts
from app.pipeline.verify.vat_type import vat_type_from_amounts

CENT = Decimal("0.01")
UAE_VAT = Decimal("0.05")
RATE_SLACK = Decimal("0.02")  # VAT worked out per line rounds a little differently

_VAT_LINE = re.compile(r"\bv\.?\s?a\.?\s?t\b|\btax\b|ضريبة", re.IGNORECASE)
_OTHER_TAX = re.compile(r"excise|tourism|municipal", re.IGNORECASE)


def remove_vat_line(invoice: Invoice) -> Invoice:
    vat = invoice.vat
    if not vat or vat < 0:
        return invoice
    copies = [i for i, line in enumerate(invoice.lines) if _names_vat(line.description)
              and line.amount == vat]  # fmt: skip
    total = invoice.total if invoice.total is not None else _evidence_total(invoice)
    if len(copies) != 1 or total is None or len(invoice.lines) < 2:
        return invoice
    lines = [line for i, line in enumerate(invoice.lines) if i != copies[0]]
    items = sum((line.amount for line in lines), Decimal(0))
    on_top = abs(items + vat - total) <= CENT
    included = abs(items - total) <= CENT
    if on_top == included or abs((total - vat) * UAE_VAT - vat) > RATE_SLACK:
        return invoice  # neither or both: never guess
    fixed = invoice.model_copy(update={"lines": lines, "subtotal": total - vat, "total": total})
    return fixed.model_copy(update={"amount_type": vat_type_from_amounts(fixed)})


def _names_vat(description: str | None) -> bool:
    return bool(description and _VAT_LINE.search(description)
                and not _OTHER_TAX.search(description))  # fmt: skip


def _evidence_total(invoice: Invoice) -> Decimal | None:
    amounts = find_amounts((invoice.evidence or {}).get("total", ""))
    return amounts.pop() if len(amounts) == 1 else None
