"""Validation rules (task 3.2, architecture §8): deterministic Python, never the model (rule 7).

One function per rule, `(invoice, context) -> issues`. Rules only flag (rule 8). The checks
that need other data come later: grounding (3.3), duplicates and credit-note originals (3.4),
the supplier's known TRN (3.5), account/tax codes (3.6), locked periods (accounting system).
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from app.core.config import Settings
from app.domain.enums import AmountType, DocumentType
from app.domain.invoice import Invoice
from app.pipeline.verify.issues import Issue, Severity
from app.pipeline.verify.normalize import (
    ISO_CURRENCY,
    find_currencies,
    is_valid_trn,
    normalize_trn,
)
from app.pipeline.verify.vat_type import vat_type_from_amounts

CENT = Decimal("0.01")
UAE_VAT_RATE = Decimal("0.05")
HOME_CURRENCY = "AED"
# A document date before this is a misread, not an old invoice (decision 28).
EARLIEST_PLAUSIBLE = date(2000, 1, 1)
OLD_AFTER_DAYS = 2 * 365  # older than this: shown, may be a misread

_TYPE_NAMES = {
    DocumentType.BILL: "bill",
    DocumentType.CREDIT_NOTE: "credit note",
    DocumentType.DEBIT_NOTE: "debit note",
    DocumentType.RECEIPT: "receipt",
    DocumentType.SALES_INVOICE: "sales invoice",
    DocumentType.NON_POSTABLE: "non-postable document",
}
# Decision 17: what every postable document needs. Receipts may lack a number (warning only).
_REQUIRED = {
    "vendor_name": "supplier name",
    "invoice_number": "document number",
    "invoice_date": "document date",
    "total": "total",
    "currency": "currency",
}


@dataclass(frozen=True, slots=True)
class RuleContext:
    folder_date: date | None  # date of the folder the file came from; None = not known
    today: date
    page_text: str = ""  # all pages, from the Read step
    vat_tolerance_per_line: Decimal = CENT
    period_max_days_before: int = 90
    future_date_slack_days: int = 1

    @classmethod
    def from_settings(
        cls, settings: Settings, *, folder_date: date | None, today: date, page_text: str
    ) -> "RuleContext":
        return cls(
            folder_date=folder_date,
            today=today,
            page_text=page_text,
            vat_tolerance_per_line=settings.vat_tolerance_per_line,
            period_max_days_before=settings.period_max_days_before,
            future_date_slack_days=settings.future_date_slack_days,
        )


Rule = Callable[[Invoice, RuleContext], list[Issue]]


def check_required(invoice: Invoice, ctx: RuleContext) -> list[Issue]:
    kind = invoice.document_type
    if kind is DocumentType.NON_POSTABLE:
        return []
    issues = []
    for field, label in _REQUIRED.items():
        if getattr(invoice, field) is not None:
            continue
        if kind is DocumentType.RECEIPT and field == "invoice_number":
            issues.append(
                _warning(
                    "receipt_number_missing",
                    field,
                    "No receipt number found. Allowed for receipts; duplicates are checked by "
                    "supplier, date and total instead.",
                )
            )
        else:
            issues.append(
                _error(
                    "required_missing",
                    field,
                    f"The {label} is required to post a {_TYPE_NAMES[kind]}.",
                )
            )
    if kind is DocumentType.CREDIT_NOTE and invoice.original_invoice_ref is None:
        issues.append(
            _warning(
                "credit_note_without_original",
                "original_invoice_ref",
                "No original invoice reference found on this credit note.",
            )
        )
    return issues


def check_totals(invoice: Invoice, ctx: RuleContext) -> list[Issue]:
    subtotal, vat, total = invoice.subtotal, invoice.vat, invoice.total
    if subtotal is None or vat is None or total is None:
        return []
    if abs(subtotal + vat - total) <= CENT:
        return []
    return [
        _error(
            "totals_mismatch",
            "total",
            f"Subtotal {subtotal} + VAT {vat} = {subtotal + vat}, but the total is {total}.",
        )
    ]


def check_line_math(invoice: Invoice, ctx: RuleContext) -> list[Issue]:
    issues = []
    for index, line in enumerate(invoice.lines):
        if line.qty is None or line.unit_price is None:
            continue
        expected = line.qty * line.unit_price
        if abs(expected - line.amount) > CENT:
            issues.append(
                _warning(
                    "line_amount_mismatch",
                    f"lines[{index}].amount",
                    f"Quantity {line.qty} x price {line.unit_price} = {expected:.2f}, "
                    f"but the line amount is {line.amount}.",
                )
            )

    if invoice.lines:
        lines_sum = sum((line.amount for line in invoice.lines), Decimal(0))
        target, label = lines_target(invoice)
        if (
            target is not None
            and abs(lines_sum - target) > CENT
            and not _vat_type_mismatch(invoice)
        ):
            issues.append(
                _warning(
                    "lines_sum_mismatch",
                    "lines",
                    f"The lines add up to {lines_sum}, but the {label} is {target}: "
                    "a missing line, a discount or rounding?",
                )
            )
    return issues


# What the amounts show, for the VAT type messages (decision 34).
_VAT_TYPE_REASONS = {
    AmountType.EXCLUSIVE: "The lines plus VAT add up to the total, so the line amounts are "
    "before VAT (VAT added on top).",
    AmountType.INCLUSIVE: "The lines add up to the total, so the line amounts already include VAT.",
    AmountType.NO_TAX: "There is no VAT and the lines add up to the total.",
}


def check_vat_type(invoice: Invoice, ctx: RuleContext) -> list[Issue]:
    """Decision 34: the VAT type must agree with the amounts. Extraction sets it from them;
    this catches edits that break the agreement, and a type nobody could work out."""
    if not invoice.lines:
        return []
    from_amounts = vat_type_from_amounts(invoice)
    suggestion = from_amounts.value if from_amounts else None
    if invoice.amount_type is None:
        if not invoice.vat:
            return []  # no VAT: whether amounts include it does not matter
        reason = (
            _VAT_TYPE_REASONS[from_amounts] + " "
            if from_amounts
            else ("It could not be worked out from the amounts. ")
        )
        return [
            Issue(
                "vat_type_missing",
                Severity.ERROR,
                "amount_type",
                f"{reason}Choose whether the line amounts include VAT: needed to post.",
                suggestion,
            )
        ]
    if from_amounts is not None and from_amounts is not invoice.amount_type:
        return [
            Issue(
                "vat_type_mismatch",
                Severity.WARNING,
                "amount_type",
                f"{_VAT_TYPE_REASONS[from_amounts]} Check the VAT type.",
                suggestion,
            )
        ]
    return []


def _vat_type_mismatch(invoice: Invoice) -> bool:
    """The VAT type rule already explains why the lines do not add up."""
    from_amounts = vat_type_from_amounts(invoice)
    return from_amounts is not None and from_amounts is not invoice.amount_type


def check_vat(invoice: Invoice, ctx: RuleContext) -> list[Issue]:
    vat = invoice.vat
    base = _amount_before_vat(invoice)
    if vat is None or base is None or base == 0:
        return []
    foreign = invoice.currency is not None and invoice.currency != HOME_CURRENCY
    if vat == 0:
        if foreign:
            return [
                _warning(
                    "vat_zero_possible_reverse_charge",
                    "vat",
                    "No VAT charged by a foreign supplier: possibly reverse charge. Check the "
                    "tax code.",
                )
            ]
        if invoice.vendor_trn is None:
            return [
                _warning(
                    "vat_zero_no_tax_invoice",
                    "vat",
                    "No VAT and no supplier VAT registration no on the document: it is not a "
                    "tax invoice, so no input VAT can be reclaimed. Check the tax code.",
                )
            ]
        return [
            _warning(
                "vat_zero",
                "vat",
                "No VAT charged although the supplier has a TRN: zero-rated or exempt? "
                "Check the tax code.",
            )
        ]

    issues = []
    expected = abs(base) * UAE_VAT_RATE
    tolerance = ctx.vat_tolerance_per_line * max(1, len(invoice.lines))
    if abs(abs(vat) - expected) > tolerance:
        rate = abs(vat) / abs(base) * 100
        issues.append(
            _error(
                "vat_rate",
                "vat",
                f"VAT is {rate:.2f}% of the amount before VAT ({base}); UAE standard VAT is 5% "
                f"(about {expected:.2f}).",
            )
        )
    if invoice.vendor_trn is None:
        issues.append(
            _warning(
                "vat_without_trn",
                "vendor_trn",
                "VAT is charged but no supplier VAT registration no was found: input VAT may "
                "not be recoverable.",
            )
        )
    return issues


def check_trn(invoice: Invoice, ctx: RuleContext) -> list[Issue]:
    issues = []
    for field, severity, label in (
        ("vendor_trn", Severity.ERROR, "Supplier VAT registration no"),
        ("recipient_trn", Severity.WARNING, "Customer TRN"),
    ):
        value = getattr(invoice, field)
        if value is None:
            continue
        digits = normalize_trn(value)
        if not is_valid_trn(digits):
            issues.append(
                Issue(
                    "trn_invalid",
                    severity,
                    field,
                    f"{label} must be 15 digits (found {len(digits)}).",
                )
            )
    vendor, recipient = invoice.vendor_trn, invoice.recipient_trn
    if vendor and recipient and normalize_trn(vendor) == normalize_trn(recipient):
        issues.append(
            _error(
                "trn_is_recipient",
                "vendor_trn",
                "The supplier VAT registration no is the same as the customer's (your own) TRN: "
                "it was probably misread.",
            )
        )
    return issues


def check_dates(invoice: Invoice, ctx: RuleContext) -> list[Issue]:
    issued, due = invoice.invoice_date, invoice.due_date
    issues = []
    if issued is not None and issued < EARLIEST_PLAUSIBLE:
        issues.append(
            _error(
                "date_implausible",
                "invoice_date",
                f"The document date {issued} cannot be right: misread date?",
            )
        )
    elif issued is not None and issued < ctx.today - timedelta(days=OLD_AFTER_DAYS):
        issues.append(
            _warning(
                "date_very_old",
                "invoice_date",
                f"The document date {issued} is more than 2 years ago: check it.",
            )
        )
    if issued is not None and issued > ctx.today + timedelta(days=ctx.future_date_slack_days):
        issues.append(
            _error(
                "date_in_future",
                "invoice_date",
                f"The document date {issued} is in the future: misread date?",
            )
        )
    if issued is not None and due is not None and due < issued:
        issues.append(
            _error(
                "due_before_issue",
                "due_date",
                f"The due date {due} is before the document date {issued}.",
            )
        )
    return issues


def check_period(invoice: Invoice, ctx: RuleContext) -> list[Issue]:
    issued, folder = invoice.invoice_date, ctx.folder_date
    if issued is None or folder is None:
        return []  # no folder date (decision 25): nothing to compare with
    if issued > folder:
        return [
            _warning(
                "date_after_folder",
                "invoice_date",
                f"The document date {issued} is after the folder date {folder}: "
                "misread date or wrong folder?",
            )
        ]
    days = (folder - issued).days
    if days > ctx.period_max_days_before:
        return [
            _warning(
                "date_long_before_folder",
                "invoice_date",
                f"The document date {issued} is {days} days before the folder date "
                f"{folder}: an old document or a misread year?",
            )
        ]
    return []


def check_currency(invoice: Invoice, ctx: RuleContext) -> list[Issue]:
    markers = find_currencies(ctx.page_text)
    currency = invoice.currency
    if currency is None:
        if markers.any:
            return [
                _warning(
                    "currency_on_page",
                    "currency",
                    f"No currency was extracted; the document shows {_names(markers.any)}.",
                )
            ]
        return []
    if not ISO_CURRENCY.fullmatch(currency):
        return [
            _error(
                "currency_invalid",
                "currency",
                f"'{currency}' is not a currency code (expected 3 letters, e.g. AED, USD).",
            )
        ]

    issues = []
    if markers.certain and currency not in markers.certain:
        issues.append(
            _warning(
                "currency_mismatch",
                "currency",
                f"The extracted currency is {currency}, but the document shows "
                f"{_names(markers.certain)}.",
            )
        )
    elif not markers.certain and markers.likely and currency not in markers.likely:
        issues.append(
            _warning(
                "currency_mismatch",
                "currency",
                f"The extracted currency is {currency}, but the document probably shows "
                f"{_names(markers.likely)} (from a symbol: '$' is usually USD; a 'D' before "
                "amounts is probably the dirham sign read by OCR).",
            )
        )
    if currency != HOME_CURRENCY:
        issues.append(
            _warning(
                "foreign_currency",
                "currency",
                f"Foreign currency ({currency}): check the exchange rate when posting.",
            )
        )
    return issues


def check_original_ref(invoice: Invoice, ctx: RuleContext) -> list[Issue]:
    if invoice.document_type not in (DocumentType.BILL, DocumentType.RECEIPT):
        return []
    if invoice.original_invoice_ref is None:
        return []
    return [
        _warning(
            "original_ref_on_bill",
            "original_invoice_ref",
            f"A {_TYPE_NAMES[invoice.document_type]} normally has no original-invoice "
            "reference: is it a credit or debit note, or was the value invented?",
        )
    ]


RULES: tuple[Rule, ...] = (
    check_required,
    check_totals,
    check_line_math,
    check_vat_type,
    check_vat,
    check_trn,
    check_dates,
    check_period,
    check_currency,
    check_original_ref,
)


def run_rules(
    invoice: Invoice | None, ctx: RuleContext, *, extraction_error: str | None = None
) -> list[Issue]:
    """All rules for one document. No invoice = the model's answer failed the schema.
    Non-postable documents (quotes, statements, ...) are parked, never posted: posting rules
    do not apply to them; only the type check (task 3.7) does."""
    if invoice is None:
        reason = extraction_error or "no answer"
        return [
            _error(
                "extraction_invalid",
                None,
                f"The extracted data could not be read ({reason}). Enter it by hand.",
            )
        ]
    if invoice.document_type is DocumentType.NON_POSTABLE:
        return []
    return [issue for rule in RULES for issue in rule(invoice, ctx)]


def lines_target(invoice: Invoice) -> tuple[Decimal | None, str]:
    """What the line amounts should add up to."""
    if invoice.amount_type is AmountType.INCLUSIVE:
        return invoice.total, "total"
    if invoice.subtotal is not None:
        return invoice.subtotal, "subtotal"
    if not invoice.vat:  # no VAT: subtotal and total are the same thing
        return invoice.total, "total"
    return None, ""


def _amount_before_vat(invoice: Invoice) -> Decimal | None:
    if invoice.subtotal is not None:
        return invoice.subtotal
    if invoice.total is not None and invoice.vat is not None:
        return invoice.total - invoice.vat
    return None


def _names(codes: frozenset[str]) -> str:
    return ", ".join(sorted(codes))


def _error(code: str, field: str | None, message: str) -> Issue:
    return Issue(code, Severity.ERROR, field, message)


def _warning(code: str, field: str | None, message: str) -> Issue:
    return Issue(code, Severity.WARNING, field, message)
