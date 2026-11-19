"""Header fields that are not what they claim (decision 90). On a handwritten pad the only
handwriting in the header is the customer's name after "Mr./M/s." and the pad's own number;
the supplier's name is not on it. The model still fills the supplier with what it finds: the
customer as OCR read it ("Roast" as IQUA), the printed title (الفاتورة as "Al Fagatoura",
INVOICE), the pad number, or a scrap. An invoice number may be a label ("Inv. No.", a pad's
"PSCIPCV" printed on every voucher) or a stray letter.

Cleared, never replaced (null over invented): the checks then ask the accountant, who knows
where it was bought (decision 88: supplier memory takes it from there). Cleared only when the
answer itself or the page text proves it:
- a supplier name with no three letters in a row, the same as the invoice number, or only a
  document title (not the same as the customer's: a till receipt names no customer, and the
  model often copies the supplier there too);
- a supplier name our OCR read right after the customer label (M/s, Mr.);
- an invoice number with no digit.
The model's own answer stays in `extractions.raw_output`.
"""

import re
from collections.abc import Sequence

from app.domain.invoice import Invoice
from app.pipeline.read import PageText

_THREE_LETTERS = re.compile(r"[^\W\d_]{3}")
_TITLE = re.compile(r"^(tax\s+)?(invoice|receipt|cash\s*memo)\S*$"
                    r"|^(al[\s-]*)?fa\w{0,3}t[ou]{1,2}rah?$", re.IGNORECASE)  # fmt: skip
# The customer label as OCR reads it, letters only: Mr./M/s -> mrms, M.M/s. -> mms, M/S -> ms.
_CUSTOMER_LABELS = ("mrms", "mms", "ms", "mr")


def clear_false_header_fields(invoice: Invoice, pages: Sequence[PageText]) -> Invoice:
    changes: dict[str, None] = {}
    if invoice.vendor_name is not None and not _may_be_supplier(invoice, pages):
        changes["vendor_name"] = None
    if invoice.invoice_number is not None and not any(c.isdigit() for c in invoice.invoice_number):
        changes["invoice_number"] = None
    return invoice.model_copy(update=changes) if changes else invoice


def _may_be_supplier(invoice: Invoice, pages: Sequence[PageText]) -> bool:
    name = invoice.vendor_name or ""
    key = _letters(name)
    if not _THREE_LETTERS.search(name) or _TITLE.match(name.strip()):
        return False
    if key == _letters(invoice.invoice_number or ""):
        return False
    return not any(written.startswith(key) for written in _after_customer_label(pages))


def _after_customer_label(pages: Sequence[PageText]) -> list[str]:
    """What OCR read after each customer label: the rest of its span, else the next span."""
    found = []
    for page in pages:
        texts = [_letters(span.text) for span in page.spans]
        for i, text in enumerate(texts):
            label = next((label for label in _CUSTOMER_LABELS if text.startswith(label)), None)
            if label is None or (len(label) == 2 and text != label):
                continue  # "ms"/"mr" alone only: "MSB Trading" is not a label
            rest = text[len(label) :]
            if rest:
                found.append(rest)
            elif i + 1 < len(texts) and texts[i + 1]:
                found.append(texts[i + 1])
    return found


def _letters(text: str) -> str:
    return "".join(c for c in text.lower() if c.isalpha())
