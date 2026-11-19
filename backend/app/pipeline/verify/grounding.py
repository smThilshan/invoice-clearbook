"""Grounding (task 3.3): every key value the model extracted must be printed on the document
(not the subtotal: worked out from the total and the VAT, decision 81).

The page text from the Read step is the independent witness (rule 7: the model never checks
itself). Values are compared after normalization (normalize.py), never as raw strings.

OCR pages only: a scan can read "1,050.10" as "1,O5O.1O"; the model, which also sees the image,
may correctly answer 1050.10. So on OCR pages letters that OCR confuses with digits (O 0,
l/I 1, S 5, B 8) next to digits are also read as digits. The exact text layer of digital PDFs
gets no such tolerance.

Decision 96, scans only: a document number or supplier TRN that OCR read with one character
unclear ("CRS/17S33", "10412603/100003") is a warning that quotes the reading, and so is a
number on a scan where OCR read no amount and no date at all. A supplier TRN with one character
unclear counts only outside a customer block (the buyer's TRN is not the supplier's).

A value not found is an error (architecture §8), except the due date (often printed as terms
like "Net 30"): a warning. Also a warning when OCR read no money amount at all (for an amount)
or no date at all (for a date) on a scan (decision 60): handwritten values, which OCR does not
read, or an unclear photo. Then there is nothing to compare with, and the accountant checks it
on the image. When OCR did read amounts or dates, a value not among them stays an error. In
review, a value the accountant confirms or types counts as human-verified (decision 24) and is
no longer grounded: pass it in `verified`.

A VAT amount must also be printed as VAT: a document with no supplier TRN that names no VAT or
tax at all (an app's order screen) has none, and its "VAT" was read from a fee (decision 47).
Pages that name VAT anywhere are not questioned (layouts vary too much to tell which amount is
the VAT), nor documents with a supplier TRN: a tax invoice, whose VAT word OCR may have lost.
"""

import re
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.domain.enums import DocumentType
from app.domain.invoice import Invoice
from app.pipeline.read import PageText
from app.pipeline.verify.charges import labelled_amounts
from app.pipeline.verify.issues import Issue, Severity
from app.pipeline.verify.layout import Line, page_lines
from app.pipeline.verify.normalize import (
    OCR_DIGITS,
    find_amounts,
    find_dates,
    fold_ocr_digits,
    normalize_trn,
    to_ascii_digits,
)
from app.pipeline.verify.trn_finder import (
    TRN_DIGITS,
    PrintedTrns,
    find_printed_trns,
    in_customer_block,
)

# Not the subtotal: it is worked out from these two (decision 81).
_AMOUNT_FIELDS = {"total": "total", "vat": "VAT amount"}
_DATE_FIELDS = {
    "invoice_date": ("document date", Severity.ERROR),
    "due_date": ("due date", Severity.WARNING),
}

# Money as printed with decimals ("75.00", "1,050.10", "3,75"): not phone or document numbers.
_MONEY = re.compile(r"(?<![\d.,])\d+(?:,\d{3})*[.,]\d{2}(?![\d.,])")
_DIGIT_RUN = re.compile(r"\d(?:[\d \-]*\d)?")

# Decision 17: a number label on the page while no number was extracted -> likely missed.
_NUMBER_LABEL = re.compile(
    r"\b(?:invoice|inv|bill|receipt|rcpt|credit\s*note|tax\s*invoice)\s*(?:no|number|num|#)"
    r"|رقم\s*(?:الفاتورة|الإيصال)",
    re.IGNORECASE,
)
# Any of these on the page: it may hold VAT (decision 47). TRN too, so a VAT word lost to OCR
# on a real tax invoice does not raise an error.
_VAT_WORDS = re.compile(
    r"\b(?:v\.?\s?a\.?\s?t|tax|gst|trn)\b|ضريبة|ضريبي|القيمة\s*المضافة", re.IGNORECASE
)


@dataclass(frozen=True, slots=True)
class _Witness:
    """Everything printed on the document, in comparable form."""

    amounts: set[Decimal]
    dates: set[date]
    compact: str  # uppercase letters and digits only, for document numbers
    compact_ocr: str  # same, with OCR digit confusions folded (OCR pages only)
    digit_runs: set[str]  # digit sequences with spaces/dashes removed, for TRNs
    raw: str
    unread_money: bool  # a scan on which OCR read no money amount ("75.00")
    unread_dates: bool  # a scan on which OCR read no date
    ocr_words: tuple[str, ...]  # OCR pages' words as read, for values with one unclear character


def check_grounding(
    invoice: Invoice,
    pages: Sequence[PageText],
    *,
    verified: Collection[str] = (),
    own_trns: Collection[str] = (),
) -> list[Issue]:
    """`own_trns`: the client's own TRNs (decision 92), to tell whose TRN is whose."""
    if invoice.document_type is DocumentType.NON_POSTABLE:
        return []  # parked, never posted (same as the validation rules)
    witness = _witness(pages)
    issues = [
        *_amounts(invoice, witness),
        *_vat_printed(invoice, pages),
        *_dates(invoice, witness),
        *_number(invoice, witness),
        *_trn(invoice, witness, find_printed_trns(pages, own_trns), pages),
    ]
    return [issue for issue in issues if issue.field not in verified]


def _witness(pages: Sequence[PageText]) -> _Witness:
    exact = "\n".join(page.text for page in pages)
    ocr = "\n".join(fold_ocr_digits(page.text) for page in pages if page.source == "ocr")
    both = to_ascii_digits(f"{exact}\n{ocr}")
    dates = find_dates(both)
    scan = bool(pages) and all(page.source == "ocr" for page in pages)
    return _Witness(
        amounts=find_amounts(both),
        dates=dates,
        compact=_compact(exact),
        compact_ocr=_compact(ocr).translate(OCR_DIGITS),
        digit_runs={re.sub(r"\D", "", run) for run in _DIGIT_RUN.findall(both)},
        raw=exact,
        unread_money=scan and not _MONEY.search(both),
        unread_dates=scan and not dates,
        ocr_words=tuple(
            word for page in pages if page.source == "ocr" for word in page.text.split()
        ),
    )


def _amounts(invoice: Invoice, witness: _Witness) -> list[Issue]:
    issues = []
    for field, label in _AMOUNT_FIELDS.items():
        value = getattr(invoice, field)
        if value is None or value == 0:  # zero VAT is often left blank
            continue
        if abs(value) not in witness.amounts:
            if witness.unread_money:
                issues.append(_unread(field, label, value, "money amount"))
            else:
                issues.append(_not_found(field, label, value, Severity.ERROR))
    return issues


def not_a_tax_invoice(invoice: Invoice, pages: Sequence[PageText]) -> bool:
    """No supplier TRN and no VAT word anywhere (decision 47): an app or shop screen that
    cannot hold VAT. Shared with extraction, which clears such a VAT (decision 52)."""
    text = "\n".join(page.text for page in pages)
    return invoice.vendor_trn is None and not _VAT_WORDS.search(text)


def _vat_printed(invoice: Invoice, pages: Sequence[PageText]) -> list[Issue]:
    """Decision 47: VAT on a document that names no VAT was read from something else."""
    vat = invoice.vat
    if vat is None or vat == 0 or not not_a_tax_invoice(invoice, pages):
        return []
    label = next((c.label for c in labelled_amounts(pages) if abs(c.amount) == abs(vat)), None)
    printed = f" {vat} is printed as '{label}'." if label else ""
    return [
        Issue(
            "vat_not_printed",
            Severity.ERROR,
            "vat",
            f"No VAT is printed on this document.{printed} Clear the VAT (an app or shop "
            "screen without VAT is not a tax invoice); a fee can then be added as a line.",
        )
    ]


def _dates(invoice: Invoice, witness: _Witness) -> list[Issue]:
    issues = []
    for field, (label, severity) in _DATE_FIELDS.items():
        value = getattr(invoice, field)
        if value is not None and value not in witness.dates:
            if witness.unread_dates:
                issues.append(_unread(field, label, value, "date"))
            else:
                issues.append(_not_found(field, label, value, severity))
    return issues


def _number(invoice: Invoice, witness: _Witness) -> list[Issue]:
    number = invoice.invoice_number
    if number is None:
        if _NUMBER_LABEL.search(witness.raw):
            return [
                Issue(
                    "number_likely_missed",
                    Severity.WARNING,
                    "invoice_number",
                    "No document number was extracted, but the page has a number label "
                    "(e.g. 'Invoice No'): check the original.",
                )
            ]
        return []
    wanted = _compact(number)
    found = bool(wanted) and (
        wanted in witness.compact or wanted.translate(OCR_DIGITS) in witness.compact_ocr
    )
    if found:
        return []
    reading = _one_unclear(wanted, witness.ocr_words, min_length=_NUMBER_MIN_UNCLEAR)
    if reading is not None:
        return [_unclear("invoice_number", "document number", number, reading)]
    if witness.unread_money and witness.unread_dates:  # OCR missed the header and the totals
        return [
            Issue(
                "not_on_document",
                Severity.WARNING,
                "invoice_number",
                f"The document number {number} could not be checked: the text reader could not "
                "read this scan (no amounts or dates). Check the number on the image.",
            )
        ]
    return [_not_found("invoice_number", "document number", number, Severity.ERROR)]


def _trn(
    invoice: Invoice, witness: _Witness, printed: PrintedTrns, pages: Sequence[PageText]
) -> list[Issue]:
    """Against the TRNs our own finder read next to their labels (decision 29) when it found
    them; otherwise the value must at least appear somewhere on the document: exactly, or on a
    scan with one character unclear, outside any customer block (decision 96)."""
    issues = []
    label = "supplier VAT registration no"
    if printed.supplier is not None and _finder_took_customer(invoice, printed.supplier, pages):
        issues.append(_whose(invoice.vendor_trn, printed.supplier))
    elif printed.supplier is not None:
        issues += _against_printed(invoice.vendor_trn, printed.supplier, "vendor_trn",
                                   label, Severity.ERROR)  # fmt: skip
    elif invoice.vendor_trn is not None:
        digits = normalize_trn(invoice.vendor_trn)
        if not (digits and digits in witness.digit_runs):
            lines = [line for page in pages for line in page_lines(page)]
            reading = _supplier_reading(lines, digits)
            if reading is not None and reading[1] is not None:
                issues.append(_unclear("vendor_trn", label, invoice.vendor_trn, reading[1]))
            else:
                issues.append(_not_found("vendor_trn", label, invoice.vendor_trn, Severity.ERROR))
    if printed.customer is not None:
        issues += _against_printed(invoice.recipient_trn, printed.customer, "recipient_trn",
                                   "customer TRN", Severity.WARNING)  # fmt: skip
    return issues


def _finder_took_customer(invoice: Invoice, supplier: str, pages: Sequence[PageText]) -> bool:
    """Decision 96: the TRN our finder took for the supplier's is the one the model read as the
    customer's, and the model's supplier TRN is printed above it on the first page, outside any
    customer block (its label misread: "[RN"). Then the finder missed the header's TRN, and
    neither reading is suggested."""
    vendor, recipient = invoice.vendor_trn, invoice.recipient_trn
    if not vendor or not recipient or normalize_trn(recipient) != supplier:
        return False
    if normalize_trn(vendor) == supplier or not pages:
        return False
    lines = page_lines(pages[0])
    reading = _supplier_reading(lines, normalize_trn(vendor))
    below = next((line.y for line in lines if supplier in _digit_runs(line)), None)
    return reading is not None and below is not None and reading[0] < below


def _supplier_reading(lines: Sequence[Line], digits: str) -> tuple[float, str | None] | None:
    """The first line outside a customer block printing these 15 digits: its height, and the
    word as read when (OCR) one character is unclear (None: printed exactly)."""
    if len(digits) != TRN_DIGITS:
        return None
    for index, line in enumerate(lines):
        words = line.text.split() if line.ocr else []
        unclear = _one_unclear(digits, words, min_length=TRN_DIGITS)
        exact = digits in _digit_runs(line)
        if (exact or unclear) and not in_customer_block(lines, index, len(line.text)):
            return line.y, None if exact else unclear
    return None


def _digit_runs(line: Line) -> set[str]:
    text = fold_ocr_digits(line.text) if line.ocr else line.text
    return {re.sub(r"\D", "", run) for run in _DIGIT_RUN.findall(text)}


def _whose(value: str | None, printed: str) -> Issue:
    return Issue(
        "trn_whose",
        Severity.WARNING,
        "vendor_trn",
        f"Two TRNs are printed: {value} above, and {printed}, the customer's. The text reader "
        f"could not read the label of the first: check that {value} is the supplier's.",
    )


def _against_printed(
    value: str | None, printed: str, field: str, label: str, severity: Severity
) -> list[Issue]:
    if value is None:
        return [Issue("trn_not_extracted", severity, field,
                      f"The {label} {printed} is printed on the document but was not "
                      "extracted.", suggestion=printed)]  # fmt: skip
    if normalize_trn(value) != printed:
        return [Issue("trn_differs_from_printed", severity, field,
                      f"The {label} printed on the document is {printed}, not {value}.",
                      suggestion=printed)]  # fmt: skip
    return []


def _compact(text: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", to_ascii_digits(text).upper())


def _not_found(field: str, label: str, value: object, severity: Severity) -> Issue:
    return Issue(
        "not_on_document",
        severity,
        field,
        f"The {label} {value} was not found on the document: check it against the original.",
    )


def _unread(field: str, label: str, value: object, kind: str) -> Issue:
    return Issue(
        "not_on_document",
        Severity.WARNING,
        field,
        f"The {label} {value} could not be checked: OCR read no {kind} on this document "
        "(handwritten or unclear). Check it on the image.",
    )


# Below this, one unclear character could make any number look like another.
_NUMBER_MIN_UNCLEAR = 6


def _one_unclear(wanted: str, words: Sequence[str], *, min_length: int) -> str | None:
    """A scanned word that reads as `wanted` (compacted) but for one character OCR did not
    read as a digit: "CRS/17S33" for CRS17933, "10412603/100003" for 104126037100003. A digit
    read as another digit is another number. The caller passes OCR words only. Returns the
    word as read."""
    if len(wanted) < min_length:
        return None
    for word in words:
        whole = to_ascii_digits(word).upper().strip(".,:;()[]")
        if _one_apart(_compact(word), wanted) or _one_apart(whole, wanted):
            return word
    return None


def _one_apart(read: str, wanted: str) -> bool:
    if len(read) != len(wanted):
        return False
    off = [
        r
        for r, w in zip(read, wanted, strict=True)
        if r != w and r.translate(OCR_DIGITS) != w.translate(OCR_DIGITS)
    ]
    return len(off) == 1 and not off[0].isdigit()


def _unclear(field: str, label: str, value: object, reading: str) -> Issue:
    return Issue(
        "not_on_document",
        Severity.WARNING,
        field,
        f"The {label} {value} could not be confirmed: the text reader read it as "
        f"'{reading}' (one character unclear on the scan). Check it on the image.",
    )
