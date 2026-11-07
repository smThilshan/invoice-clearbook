"""Bill or expense (decision 88): every purchase document is one or the other, always chosen.

A bill is bought on account and paid later; an expense was paid when bought (cash, card,
petty cash). The page rarely says which in words that can be trusted: shop slips are titled
"TAX INVOICE", and account suppliers print "CASH" for their van-sales customers (M.H.
Enterprises: "Payment Term CASH", "CASH SALES", yet a bill). What tells them apart is whether
the supplier knows the buyer as an account customer. Decided by code, never by the model
(rule 7); the first step that applies wins:

1. The supplier's memory: the type the accountant approved for it (`vendor_mappings`).
2. Proof of payment: cash given and change, a card approval, "Paid with Visa", "Payment
   received". Expense, unless the page is an account supplier's (then step 4 decides).
3. Credit or due terms printed: "30 days", "Net due", a due date. Bill.
4. An account supplier: customer code, route or trip, salesman, order or LPO number; with a
   delivery block or the buyer's TRN as weaker support. Bill.
5. A shop, transport or restaurant supplier, by its own name (supermarket, RTA, taxi, Salik,
   petrol, restaurant, Noon, Talabat ...). Expense.
6. One account clue alone: bill, not sure.
7. Nothing: expense, not sure. In the UAE a credit supplier always names its customer on the
   invoice (code, salesman, TRN); without that there is no account, so it was paid.

How sure (`Level`): the supplier's memory and a proven payment are PROVEN; the other strong
steps are SURE, and stay quiet only when the model read the same type; a weak step is WEAK.
The rest get a warning (`payment_issue`) with the other type one click away. A bill needs the
supplier's invoice number; an expense does not (checks, `require_number`).

Expense is stored as RECEIPT (no enum or data change): the Workbench calls it Expense, and
both are posted as bills until expenses are posted as expenses (decision 50).
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from app.domain.enums import DocumentType
from app.domain.invoice import Invoice
from app.pipeline.paid import Proof, proven_payment
from app.pipeline.read import PageText
from app.pipeline.verify.issues import Issue, Severity

PURCHASES = frozenset({DocumentType.BILL, DocumentType.RECEIPT})
EXPENSE = DocumentType.RECEIPT

# A label's value: holds a digit ("OC7219", "CFDSO-2610056219"), not the next label.
_VALUE = r"[:\-.#]?\s*(?=[\w\-/]*\d)[\w\-/]{4,}"
_ACCOUNT = {
    "customer code": re.compile(
        r"\bcust(?:omer)?\s*(?:id|code|no|a/?c|account)?\s*" + _VALUE
        + r"|\bbill\s*to\s*[:\-]?\s*[a-z]{2,4}\d{3,}",
        re.I,
    ),
    "route": re.compile(r"\broute\s*(?:code|no)?\s*" + _VALUE + r"|\btrip\s*id\s*" + _VALUE, re.I),
    "salesman": re.compile(r"\bsales\s*man\b", re.I),
    "order number": re.compile(
        r"\b(?:order|p\.?o|l\.?p\.?o)\s*(?:no|number|#)\.?\s*" + _VALUE, re.I
    ),
}  # fmt: skip
_DELIVERY = re.compile(
    r"\bdelivery\s*(?:date|note|locn|location|no)\b|\bdely\.?\s*locn\b|\bvehicle\s*no\b"
    r"|\breceiver'?s?\s*sign",
    re.I,
)
_TRN = re.compile(r"(?<!\d)1\d{14}(?!\d)")
_CREDIT = re.compile(
    r"\b(?:payment\s*terms?|terms\s*of\s*payment|mode\s*of\s*payment|pay(?:ment)?\s*(?:mode|type))"
    r"\s*[:;\-]?\s*(?:credit(?!\s*card)|\d{1,3}\s*days)"
    r"|\bcredit\s*(?:sale|invoice|terms|customer)\b|\bon\s*credit\b|\b\d{1,3}\s*days\s*credit\b"
    r"|\bnet\s*\d{1,3}\b|\bnet\s*due\b"
    # a due date, not a till's "Change Due: 0.50" or "Amount Due 10.00"
    r"|\bdue(?:\s*date)?\s*[:\-]?\s*\d{1,2}[/\-.](?:\d{1,2}|[a-z]{3})[/\-.]\d{2,4}",
    re.I,
)
_PAID = re.compile(
    r"\bauth(?:ori[sz]ation)?\.?\s*(?:code|no|#)|\bapproval\s*code|\bappr\s*code"
    r"|\bcontactless\b|\bcard\s*(?:no|number|ending|type|payment)\b|\bvisa\b|\bmaster\s*card\b"
    r"|\bamex\b|\b(?:apple|google|samsung)\s*pay\b|[x*]{4,}\s?\d{4}\b"
    r"|\baccepted\b|\bmerchant\s*(?:#|no|id)|\bbank\s*card\b|\bpaid\s*(?:with|by|in)\b"
    r"|\bpayment\s*received\b|\bpaid\b|\btender(?:ed)?\b|\bchange\s*due\b",
    re.I,
)
_SHOP = re.compile(
    r"super\s*market|hyper\s*market|mini\s*mart|grocer|baqala|bakala|carrefour|\blulu\b"
    r"|spinneys|waitrose|union\s*co-?op|\bco-?op\b|\bviva\b|\bnoon\b|amazon|talabat|deliveroo"
    r"|instashop|careem|\buber\b|taxi|\brta\b|roads\s*(?:and|&)\s*transport|\bnol\b|\bsalik\b"
    r"|parking|petrol|\benoc\b|\badnoc\b|\beppco\b|\bemarat\b|restaurant|cafeteria|\bcaf[eé]\b"
    r"|pharmacy",
    re.I,
)


class Level(StrEnum):
    PROVEN = "PROVEN"
    SURE = "SURE"
    WEAK = "WEAK"


@dataclass(frozen=True, slots=True)
class PaymentDecision:
    document_type: DocumentType  # BILL or RECEIPT (shown as Expense)
    reason: str  # for the accountant: "account supplier: route, salesman"
    level: Level


def decide_payment(
    invoice: Invoice, pages: Sequence[PageText], *, remembered: DocumentType | None = None
) -> PaymentDecision | None:
    """None for anything but a bill or an expense (credit notes, non-postable ...)."""
    if invoice.document_type not in PURCHASES:
        return None
    if remembered in PURCHASES:
        return PaymentDecision(remembered, "as approved before for this supplier", Level.PROVEN)
    text = "\n".join(page.text for page in pages)
    account = [name for name, pattern in _ACCOUNT.items() if pattern.search(text)]
    support = (
        account + ["delivery"] * bool(_DELIVERY.search(text)) + ["buyer's TRN"] * _buyer_trn(text)
    )
    paid = _payment(invoice, pages, text, by_label=not account)

    if paid and not account:
        return PaymentDecision(EXPENSE, f"paid: {paid}", Level.PROVEN)
    if not paid and _CREDIT.search(text):
        return PaymentDecision(DocumentType.BILL, "credit or due terms printed", Level.SURE)
    if account and len(support) >= 2:
        return PaymentDecision(
            DocumentType.BILL,
            "account supplier: " + ", ".join(support),
            Level.WEAK if paid else Level.SURE,
        )
    if invoice.vendor_name and _SHOP.search(invoice.vendor_name):
        return PaymentDecision(EXPENSE, "shop, transport or restaurant", Level.SURE)
    if account:
        return PaymentDecision(DocumentType.BILL, "account supplier: " + account[0], Level.WEAK)
    if paid:
        return PaymentDecision(EXPENSE, f"paid: {paid}", Level.WEAK)
    return PaymentDecision(EXPENSE, "no customer account or credit terms printed", Level.WEAK)


def payment_issue(decision: PaymentDecision, model_type: DocumentType | None) -> list[Issue]:
    """The warning asking the accountant to look, with the other type one click away; none when
    proven, or sure and the model read the same type (`model_type`: its own answer)."""
    if decision.level is Level.PROVEN or (
        decision.level is Level.SURE and model_type in (None, decision.document_type)
    ):
        return []
    chosen, other = (
        ("an expense", DocumentType.BILL)
        if decision.document_type is EXPENSE
        else ("a bill", EXPENSE)
    )
    doubt = (
        "the model read it the other way"
        if decision.level is Level.SURE
        else "few signs on the page"
    )
    return [
        Issue(
            "bill_or_expense",
            Severity.WARNING,
            "document_type",
            f"Chosen as {chosen} ({decision.reason}), but {doubt}: check it.",
            suggestion=other.value,
        )
    ]


def _payment(invoice: Invoice, pages: Sequence[PageText], text: str, *, by_label: bool) -> str:
    """What proves the payment, or "". A total printed as money given ("CASH: 49.35") is no
    proof on an account supplier's invoice: Al Ain prints it for its van-sales customers."""
    proof = proven_payment(invoice, pages)
    if proof is Proof.CHANGE or (proof is Proof.LABEL and by_label):
        return proof.value
    found = _PAID.search(text)
    return f'"{" ".join(found.group().split())}"' if found else ""


def _buyer_trn(text: str) -> bool:
    """Two different TRNs: the supplier's and the buyer's (a full tax invoice to a business)."""
    return len(set(_TRN.findall(re.sub(r"[ \-]", "", text)))) >= 2
