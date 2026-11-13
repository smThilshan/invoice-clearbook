"""Type check (task 3.7, decision 25): does the content confirm the model's document type?

Documents are purchase-side by default. Two independent opinions are compared, never merged:
the model's `document_type`, and title words found deterministically in the title area (the
top of the first page). Only the title area counts, so small print such as "a credit note will
be issued for returns" on a bill is not mistaken for a title.

- Model and title both say non-postable (quote, statement, ...): parked, never posted.
- Only one of them says non-postable, or bill vs credit note disagree: error (Exception).
- The title names an invoice and a delivery note ("Tax Invoice / Delivery Note"): a warning,
  not an error (decision 95). A quote, order, statement or pro-forma stays an error.
- The header's grid of boxes to fill (Tally's layout: "Delivery Note", "Delivery Note Date",
  "Buyer's Order No.", "Terms of Delivery" ...) is not a title: its "Delivery Note" box is
  ignored when two or more of those labels are found (decision 95; KSGN, Sparklean).
- Debit notes and sales invoices: not in the MVP scope, error with the reason.
- Bill or expense (paid or owed?) is not decided here: `pipeline/payment.py` (decision 88).
The type itself is never changed here (rule 8). When the model says
non-postable but nothing confirms it, the error offers the type the document shows: a
receipt when it shows a payment (an app's order screen: "Order Details", "Payment method
Cash", "Amount Paid"; decision 48), else the type its title names.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass

from app.domain.enums import DocumentType
from app.domain.invoice import Invoice
from app.pipeline.read import PageText
from app.pipeline.verify.issues import Issue, Severity

TITLE_AREA = 0.35  # top part of the first page, as a fraction of its height

# Checked in this order: the more specific titles first ("credit note" before "invoice").
_TITLES: tuple[tuple[DocumentType, re.Pattern[str]], ...] = (
    (DocumentType.CREDIT_NOTE, re.compile(r"\bcredit\s*(?:note|memo)\b|إشعار\s*دائن", re.I)),
    (DocumentType.DEBIT_NOTE, re.compile(r"\bdebit\s*note\b|إشعار\s*مدين", re.I)),
    (
        DocumentType.NON_POSTABLE,
        re.compile(
            r"\b(?:quotation|quote|pro[\s-]?forma|purchase\s*order|statement\s*of\s*account"
            r"|account\s*statement|delivery\s*(?:note|order)|remittance(?:\s*advice)?)\b"
            r"|عرض\s*سعر|أمر\s*شراء|كشف\s*حساب|إشعار\s*تسليم",
            re.I,
        ),
    ),
    (DocumentType.RECEIPT, re.compile(r"\breceipt\b|إيصال", re.I)),
    (DocumentType.BILL, re.compile(r"\binvoice\b|\bbill\b|فاتورة", re.I)),
)
# The labels of the header's grid of boxes (Tally's layout). Each counted once; OCR may garble
# some ("Despatd'ed through"), so two of them are enough.
_FORM_LABELS = (
    re.compile(r"\bdelivery\s*note\s*date\b", re.I),
    re.compile(r"\bbuyer'?s\s*order\s*no\b", re.I),
    re.compile(r"\bdespatch\s*document\s*no\b", re.I),
    re.compile(r"\bdespatched\s*through\b", re.I),
    re.compile(r"\bterms\s*of\s*delivery\b", re.I),
    re.compile(r"\bsupplier'?s\s*ref\b", re.I),
    re.compile(r"\bother\s*reference", re.I),
)
_FORM_MIN_LABELS = 2
_DELIVERY_NOTE = re.compile(r"\bdelivery\s*(?:note|order)\b", re.I)
_NOT_A_BILL = re.compile(  # with an invoice title, still never posted
    r"\b(?:quotation|quote|pro[\s-]?forma|purchase\s*order|statement|remittance)\b", re.I
)
# Signs that the document was paid on the spot (a receipt rather than a bill to pay).
_PAYMENT_MARKS = re.compile(
    r"\b(?:paid|cash|change\s*due|change|tender(?:ed)?|visa|mastercard|amex|card\s*payment"
    r"|credit\s*card|debit\s*card|card\s*no|card\s*ending|(?:apple|google|samsung)\s*pay)\b",
    re.I,
)
_OUT_OF_SCOPE = {
    DocumentType.DEBIT_NOTE: "Debit notes are not handled automatically yet: process it by hand.",
    DocumentType.SALES_INVOICE: "Sales invoices are not in scope (purchase-side only): "
    "process it by hand.",
}
_NAMES = {
    DocumentType.BILL: "a bill/invoice",
    DocumentType.CREDIT_NOTE: "a credit note",
    DocumentType.DEBIT_NOTE: "a debit note",
    DocumentType.RECEIPT: "an expense",
    DocumentType.SALES_INVOICE: "a sales invoice",
    DocumentType.NON_POSTABLE: "a non-postable document (quote, order, statement, ...)",
}


@dataclass(frozen=True, slots=True)
class TypeCheck:
    issues: list[Issue]
    park: bool  # model and title agree it is non-postable: park it, never post


def title_signals(pages: Sequence[PageText]) -> set[DocumentType]:
    """Document types named in the title area of the first page."""
    return {kind for kind, pattern in _TITLES if pattern.search(_title(pages))}


def _title(pages: Sequence[PageText]) -> str:
    """The title area's text, without the "Delivery Note" box of a header grid."""
    if not pages:
        return ""
    title = " ".join(span.text for span in pages[0].spans if span.box[1] < TITLE_AREA)
    if sum(bool(label.search(title)) for label in _FORM_LABELS) >= _FORM_MIN_LABELS:
        return _DELIVERY_NOTE.sub(" ", title)
    return title


def check_document_type(
    invoice: Invoice, pages: Sequence[PageText], *, confirmed: bool = False
) -> TypeCheck:
    """`confirmed`: the accountant chose the type in review (decision 24). Title signals no
    longer question it, but a type we do not post yet is still reported."""
    model = invoice.document_type
    if confirmed and model not in _OUT_OF_SCOPE:
        return TypeCheck([], park=model is DocumentType.NON_POSTABLE)
    titles = title_signals(pages)
    named = _strongest(titles)

    if model is DocumentType.NON_POSTABLE:
        if DocumentType.NON_POSTABLE in titles:
            return TypeCheck([], park=True)
        reason = f"but its title says {_NAMES[named]}" if named else "but its title does not say so"
        if shows_payment(pages):
            return TypeCheck(
                [
                    _conflict(
                        f"The model read it as {_NAMES[model]}, {reason} and it shows a "
                        "payment (cash/card/paid): it looks like an expense.",
                        suggestion=DocumentType.RECEIPT,
                    )
                ],
                park=False,
            )
        return TypeCheck(
            [
                _conflict(
                    f"The model read it as {_NAMES[model]}, {reason}. "
                    "Check before it is posted or parked.",
                    suggestion=named,
                )
            ],
            park=False,
        )

    if model in _OUT_OF_SCOPE:
        return TypeCheck(
            [Issue("type_out_of_scope", Severity.ERROR, "document_type", _OUT_OF_SCOPE[model])],
            park=False,
        )

    issues: list[Issue] = []
    if DocumentType.NON_POSTABLE in titles and _invoice_and_delivery_note(pages, titles):
        issues.append(
            Issue(
                "type_delivery_note",
                Severity.WARNING,
                "document_type",
                "The title says an invoice and also a delivery note: check it is the invoice "
                "(with prices and VAT), not only the delivery note.",
            )
        )
    elif DocumentType.NON_POSTABLE in titles:
        issues.append(
            _conflict(
                f"The title says {_NAMES[DocumentType.NON_POSTABLE]}, but the "
                f"model read it as {_NAMES[model]}. It must not be posted "
                "unless it really is a bill."
            )
        )
    elif named in (DocumentType.CREDIT_NOTE, DocumentType.DEBIT_NOTE) and model is not named:
        issues.append(
            _conflict(f"The title says {_NAMES[named]}, but the model read it as {_NAMES[model]}.")
        )
    elif model is DocumentType.CREDIT_NOTE and titles and DocumentType.CREDIT_NOTE not in titles:
        issues.append(
            _conflict(
                f"The model read it as a credit note, but the title says "
                f"{_NAMES[named or DocumentType.BILL]}."
            )
        )
    return TypeCheck(issues, park=False)


def _invoice_and_delivery_note(pages: Sequence[PageText], titles: set[DocumentType]) -> bool:
    """One document as both ("Tax Invoice / Delivery Note"); never a quote, order or statement."""
    title = _title(pages)
    return (
        bool(titles & {DocumentType.BILL, DocumentType.RECEIPT})
        and bool(_DELIVERY_NOTE.search(title))
        and not _NOT_A_BILL.search(title)
    )


def shows_payment(pages: Sequence[PageText]) -> bool:
    return any(_PAYMENT_MARKS.search(page.text) for page in pages)


def _strongest(titles: set[DocumentType]) -> DocumentType | None:
    """The most specific title type found (credit note beats invoice, and so on)."""
    return next((kind for kind, _ in _TITLES if kind in titles), None)


def _conflict(message: str, suggestion: DocumentType | None = None) -> Issue:
    """`suggestion`: the type the document shows, offered in review (never applied, rule 8)."""
    return Issue("type_conflict", Severity.ERROR, "document_type", message,
                 suggestion.value if suggestion else None)  # fmt: skip
