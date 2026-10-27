"""Printed TRNs (decision 29): our own reading of the supplier's and customer's TRN, to check
the model's (rule 7: the model never checks itself). Text matching only, no model call.

    VAT REGISTRATION NO:100296342700003   EXCISE NO:1000523074    <- header: the supplier
    TRN : 10012S45S789012                                          <- customer block: the customer

- A TRN is exactly 15 digits, printed after a label: TRN, VAT Registration No, Tax Registration
  No/Number, VAT No, TIN, الرقم الضريبي. Small misspellings of REGISTRATION from scanners
  ("REGISTRALION") still count. Excise, licence or phone numbers have other labels: never taken.
- On OCR text (ours or a scanner's) letters read for digits are corrected (S=5, O=0, l=1).
- Whose: a TRN in a customer block (Customer, Bill to, Sold to, ...) is the customer's; a shop
  slip's "CUSTOMER COPY" (the copy handed to the customer; OCR: "CORY") is not a customer
  block. Of the other labels, the highest on the first page is the supplier's (its header),
  even when its number cannot be read (a scanner's "10029634270000?"): then the supplier TRN
  is unknown, and the next TRN down is not mistaken for it. With exactly two TRN labels and no
  customer label, the lower one is the customer's.
- Not sure or unreadable = None: the caller falls back to the plain grounding check.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass

from app.pipeline.read import PageText
from app.pipeline.verify.layout import Line, page_lines
from app.pipeline.verify.normalize import OCR_DIGITS

TRN_DIGITS = 15
# Customer block: a customer label at most this far above the TRN line, in the same column.
_BLOCK_HEIGHT = 0.12
_COLUMN_SLACK = 0.15

_LABEL = re.compile(
    r"(?:\bT\.?\s?R\.?\s?N\b(?:\s*[A-Z]{2,8}\.?(?=\s*[:#\-]))?|\bTIN\b"
    r"|\bVAT\s*(?:REG[A-Z]*\.?\s*)?(?:NO|NUMBER|NUM|#)(?![A-Z])"
    r"|\bTAX\s*REG[A-Z]*\.?(?:\s*(?:NO|NUMBER|NUM|#)(?![A-Z]))?"
    r"|الرقم\s*الضريبي)"
    r"\.?(?:\s*(?:NO|NUMBER|#)(?![A-Z]))?\s*[:.#\-]*\s*",
    re.IGNORECASE,
)
_CUSTOMER = re.compile(
    r"\b(?:CUSTOMER(?!\s*C[O0][PR]Y)|CLIENT|BUYER|PURCHASER|RECIPIENT|CONSIGNEE"
    r"|(?:BILL|SOLD|SHIP|INVOICE)\s*TO|DELIVER(?:Y|ED)?\s*TO)\b|العميل",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class PrintedTrns:
    supplier: str | None  # 15 digits
    customer: str | None


@dataclass(frozen=True, slots=True)
class _Label:
    digits: str | None  # None: label printed, number unreadable
    line: Line
    customer_block: bool


def find_printed_trns(pages: Sequence[PageText]) -> PrintedTrns:
    lines = [line for page in pages for line in page_lines(page)]
    labels: list[_Label] = []
    for index, line in enumerate(lines):
        for match in _LABEL.finditer(line.text):
            digits = _number_after(line.text[match.end() :], ocr=line.ocr)
            if digits is None or all(label.digits != digits for label in labels):
                block = _in_customer_block(lines, index, match.start())
                labels.append(_Label(digits, line, block))

    customer = [label for label in labels if label.customer_block]
    others = [label for label in labels if not label.customer_block]  # top to bottom
    supplier = others[0] if others and others[0].line.page == 1 else None
    if not customer and len(labels) == 2 and supplier is not None:
        customer = [others[1]]  # two parties on a bill: the lower TRN is the customer's
    return PrintedTrns(
        supplier=supplier.digits if supplier else None,
        customer=customer[0].digits if len(customer) == 1 else None,
    )


def _number_after(text: str, *, ocr: bool) -> str | None:
    """Exactly 15 digits right after a label, possibly split by spaces or dashes."""
    digits = ""
    for token in text.split():
        if not re.search(r"\d", token):
            break  # a word: the number has ended
        if ocr:
            token = token.translate(OCR_DIGITS)
        digits += re.sub(r"\D", "", token)
        if len(digits) >= TRN_DIGITS:
            break
    return digits if len(digits) == TRN_DIGITS else None


def _in_customer_block(lines: Sequence[Line], index: int, label_at: int) -> bool:
    line = lines[index]
    if _CUSTOMER.search(line.text[:label_at]):
        return True
    return any(
        _CUSTOMER.search(above.text)
        for above in lines[:index]
        if above.page == line.page
        and 0 < line.y - above.y <= _BLOCK_HEIGHT
        and abs(above.x - line.x) <= _COLUMN_SLACK
    )
