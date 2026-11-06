"""Verify step (task 3.8): every check of Phase 3 on an EXTRACTED document, then the queue.

    coding (3.6, the only part that may ask the model)
    + rules (3.2) + grounding (3.3) + duplicates (3.4) + supplier match (3.5)
    + the codes rule (3.6) + type check (3.7)
    -> issues, codes, supplier, business key saved on the document
    -> queue: any error = Exception, only warnings = Check, nothing = Ready
    -> EXTRACTED -> VERIFIED -> IN_REVIEW, or -> NON_POSTABLE when parked

The checks after coding are `assess`, shared with `recheck_document`: after an accountant's
edit in review (task 4.2) they run again, deterministic and without the model, and fields the
accountant typed or confirmed are no longer grounded (decision 24).

Nothing here changes an extracted value (rule 8); the coded invoice only adds account and tax
codes to the lines; an empty VAT type may get a likely one (decision 84) and the date the one
every clue agrees on (decision 85), each with a warning.
An unreadable extraction still goes to review (Exception, "enter it by hand"). If the model is
down, coding leaves accounts for review with a warning; verification itself never needs the
model to succeed.
"""

import json
import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import date
from enum import StrEnum

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.accounting.registry import PROVIDERS
from app.adapters.llm import ExtractionClient
from app.adapters.ocr import OcrEngine
from app.adapters.storage import Storage
from app.core.config import Settings
from app.domain.enums import (
    AccountingProvider,
    AmountType,
    DocumentStatus,
    QueueCategory,
    TaxType,
)
from app.domain.invoice import Invoice, Line
from app.domain.state_machine import transition
from app.models import Client, Document, Extraction
from app.pipeline import coding
from app.pipeline.dates import DateLimits, folder_for, photo_date, resolve_date
from app.pipeline.folders import folder_year_known
from app.pipeline.page_quality import quality_issues
from app.pipeline.read import PageText, decode_image, load_page_texts
from app.pipeline.subtotal import with_worked_out_subtotal
from app.pipeline.total_line import total_line
from app.pipeline.verify.charges import ensure_charge_text, find_charges, load_charge_text
from app.pipeline.verify.duplicates import check_duplicates, claim_keys, vendor_key
from app.pipeline.verify.grounding import check_grounding
from app.pipeline.verify.issues import Issue, Severity
from app.pipeline.verify.rules import RuleContext, check_vat, run_rules
from app.pipeline.verify.supplier_history import SupplierHistory, load_supplier_history
from app.pipeline.verify.type_check import check_document_type
from app.pipeline.verify.vat_type import likely_vat_type, vat_type_from_amounts
from app.pipeline.verify.vendor_match import match_vendor

logger = logging.getLogger(__name__)

ACTOR = "system:verify"


class VerifyOutcome(StrEnum):
    IN_REVIEW = "in_review"
    PARKED = "parked"
    SKIPPED = "skipped"  # not EXTRACTED


@dataclass(frozen=True, slots=True)
class VerifyResult:
    outcome: VerifyOutcome
    queue: QueueCategory | None = None
    errors: int = 0
    warnings: int = 0


@dataclass(frozen=True, slots=True)
class Assessment:
    """What the checks found; already saved on the document."""

    queue: QueueCategory
    errors: int
    warnings: int
    park: bool  # model and title agree it is non-postable (or the accountant said so)


def verify_document(
    session: Session,
    storage: Storage,
    client: ExtractionClient,
    document: Document,
    *,
    settings: Settings,
    today: date,
    ocr: Callable[[], OcrEngine] | None = None,
) -> VerifyResult:
    """`ocr` (made only if needed): our own OCR for documents whose total does not add up,
    to find charges outside the lines (task 4.13)."""
    if document.status is not DocumentStatus.EXTRACTED:
        return VerifyResult(VerifyOutcome.SKIPPED)

    started = time.perf_counter()
    provider = _provider(session, document, settings)
    chart = coding.load_chart(session, client_id=document.client_id, provider=provider)
    invoice = working_copy(document)
    coding_issues: list[Issue] = []
    coding_seconds = 0.0
    if invoice is not None:
        coding_started = time.perf_counter()
        coded = coding.code_document(
            session,
            invoice,
            chart,
            client,
            client_id=document.client_id,
            vendor_key=vendor_key(invoice),
            min_consistent_approvals=settings.coding_min_consistent_approvals,
        )
        coding_seconds = time.perf_counter() - coding_started
        invoice = coded.apply(invoice)
        coding_issues = coded.issues
        document.coding = [line.to_json() for line in coded.lines]
        if ocr is not None:
            try:  # best effort: without it the totals error simply stays as it is
                ensure_charge_text(storage, document, invoice, load_page_texts(storage, document),
                                   ocr, settings.charge_hint_words, decode_image)  # fmt: skip
            except Exception as exc:
                logger.warning("document %s: charge OCR failed (%s)", document.id,
                               type(exc).__name__)  # fmt: skip

    result = assess(
        session, storage, document, invoice, chart, provider,
        settings=settings, today=today, extra_issues=coding_issues,
    )  # fmt: skip

    summary = {"queue": result.queue.value, "errors": result.errors, "warnings": result.warnings}
    timed = {  # step timings (4.9): coding is the model's part of this step
        **summary,
        "work_ms": round((time.perf_counter() - started) * 1000),
        "coding_ms": round(coding_seconds * 1000),
    }
    transition(session, document, DocumentStatus.VERIFIED, actor=ACTOR, details=timed)
    if result.park:
        transition(
            session,
            document,
            DocumentStatus.NON_POSTABLE,
            actor=ACTOR,
            reason="non-postable document (title and model agree)",
        )
        return VerifyResult(VerifyOutcome.PARKED, result.queue, result.errors, result.warnings)
    transition(session, document, DocumentStatus.IN_REVIEW, actor=ACTOR, details=summary)
    return VerifyResult(VerifyOutcome.IN_REVIEW, result.queue, result.errors, result.warnings)


def recheck_document(
    session: Session, storage: Storage, document: Document, *, settings: Settings, today: date
) -> Assessment:
    """Run the checks again on the stored working copy (after a review edit). No model call,
    no coding and no status change: codes are now the accountant's, parking is theirs too."""
    provider = _provider(session, document, settings)
    chart = coding.load_chart(session, client_id=document.client_id, provider=provider)
    return assess(session, storage, document, working_copy(document), chart, provider,
                  settings=settings, today=today)  # fmt: skip


def code_empty_lines(
    session: Session,
    document: Document,
    invoice: Invoice,
    client: ExtractionClient,
    *,
    settings: Settings,
) -> Invoice:
    """Coding in review: for a document taken out of non-postable (verify skipped it then), or
    one whose accounts the accounting system no longer takes on a bill line. Fills the tax code
    nobody chose yet and every account that is empty or unusable; the rest stays theirs."""
    chart = coding.load_chart(session, client_id=document.client_id,
                              provider=_provider(session, document, settings))  # fmt: skip
    # Coding reads a tax type already on a line as one the page gave (a rounding, decision 58)
    # and gives that line the goods' account. Once every line was coded, every line has one:
    # coded afresh, and the tax types chosen stay below.
    fresh = invoice
    if all(line.tax_type is not None for line in invoice.lines):
        fresh = invoice.model_copy(
            update={"lines": [line.model_copy(update={"tax_type": None}) for line in invoice.lines]}
        )
    coded = coding.code_document(
        session,
        fresh,
        chart,
        client,
        client_id=document.client_id,
        vendor_key=vendor_key(invoice),
        min_consistent_approvals=settings.coding_min_consistent_approvals,
    )
    if not coded.lines:
        return invoice
    pickable = bool(chart.purchase_accounts())
    lines, suggestions = [], []
    for line, suggested in zip(invoice.lines, coded.lines, strict=True):
        kept = chart.usable(line.account_code) or (line.account_code and not pickable)
        if kept:  # chosen by the accountant: not a suggestion
            suggested = replace(suggested, account_code=line.account_code,
                                account_source=coding.Source.NONE,
                                account_confidence=coding.Confidence.NONE)  # fmt: skip
        if line.tax_type is not None:
            suggested = replace(suggested, tax_type=line.tax_type,
                                tax_code=chart.tax_codes.get(line.tax_type),
                                tax_confidence=coding.Confidence.NONE)  # fmt: skip
        lines.append(line.model_copy(update={"account_code": suggested.account_code,
                                             "tax_type": suggested.tax_type}))  # fmt: skip
        suggestions.append(suggested.to_json())
    document.coding = suggestions
    return invoice.model_copy(update={"lines": lines})


def has_unusable_accounts(
    session: Session, document: Document, invoice: Invoice, *, settings: Settings
) -> bool:
    """A line holds an account the client's accounting system will not take on a bill line
    (removed there, or a cash or bank account): coding picks again (decision 74)."""
    chart = coding.load_chart(session, client_id=document.client_id,
                              provider=_provider(session, document, settings))  # fmt: skip
    if not chart.purchase_accounts():
        return False  # nothing to pick from (no master data yet): the choices stay
    chosen = [line.account_code for line in invoice.lines if line.account_code is not None]
    return any(not chart.usable(code) for code in chosen)


def assess(
    session: Session,
    storage: Storage,
    document: Document,
    invoice: Invoice | None,
    chart: coding.ChartOfAccounts,
    provider: AccountingProvider,
    *,
    settings: Settings,
    today: date,
    extra_issues: Sequence[Issue] = (),
) -> Assessment:
    """Every deterministic check on `invoice`; saves invoice, issues, queue, supplier and keys
    on the document."""
    if invoice is not None:  # decision 81: also for documents saved before it
        invoice = with_worked_out_subtotal(invoice)
    pages = load_page_texts(storage, document)
    verified = frozenset(document.verified_fields or ())
    filled: list[Issue] = []  # values chosen from the evidence, each with its note
    if invoice is not None:
        history = load_supplier_history(session, document, invoice)
        invoice, filled = _likely_vat_type(invoice, verified, history)
        invoice, dated = _resolved_date(session, document, invoice, pages, verified, history,
                                        settings=settings, today=today)  # fmt: skip
        filled += dated
    issued = invoice.invoice_date if invoice else None
    folder = folder_for(issued, document.folder_date,
                        known=folder_year_known(document.source_path))  # fmt: skip
    context = RuleContext.from_settings(
        settings,
        folder_date=folder,
        today=today,
        page_text="\n".join(page.text for page in pages),
    )

    error = _last_extraction_error(session, document) if invoice is None else None
    issues = require_number(run_rules(invoice, context, extraction_error=error), provider)
    issues += quality_issues([(page.page, page.quality) for page in pages])
    issues += filled
    park = False
    if invoice is not None:
        issues += check_grounding(invoice, pages, verified=verified)

        # Keys are per type (decision 18): a type changed in review (a bill now a receipt)
        # releases the old key before the new type is saved, then claims one for it. Else the
        # unique index saw the new type with the old key, or the old type with the new one.
        if document.document_type is not invoice.document_type:
            document.business_key = None
        document.document_type = invoice.document_type
        duplicates = check_duplicates(session, document, invoice)
        issues += duplicates.issues
        if not claim_keys(session, document, duplicates):
            issues.append(
                Issue(
                    "duplicate",
                    Severity.ERROR,
                    "invoice_number",
                    "Another copy of this document was verified at the same time: one of them "
                    "is a duplicate.",
                )
            )

        supplier = match_vendor(
            session,
            invoice,
            client_id=document.client_id,
            provider=provider,
            min_similarity=settings.vendor_match_min_similarity,
            suggest_similarity=settings.vendor_suggest_min_similarity,
        )
        document.contact_id = supplier.contact_id
        issues += supplier.issues

        issues += [*extra_issues, *coding.check_codes(invoice, chart)]
        document.invoice = json.loads(invoice.model_dump_json())  # money as exact text

        types = check_document_type(invoice, pages, confirmed="document_type" in verified)
        issues += types.issues
        park = types.park

    if invoice is not None:
        issues = _explain_total(storage, document, invoice, pages, issues, settings, context)

    errors = sum(issue.severity is Severity.ERROR for issue in issues)
    warnings = len(issues) - errors
    queue = (
        QueueCategory.EXCEPTION
        if errors
        else QueueCategory.CHECK
        if warnings
        else QueueCategory.READY
    )
    document.issues = [issue.to_json() for issue in issues]
    document.queue = queue
    return Assessment(queue, errors, warnings, park)


TOTAL_GAP_CODES = frozenset({"lines_sum_mismatch", "total_line"})  # charges explain either
VAT_CODES = frozenset({"vat_rate", "total_before_vat"})  # the VAT is not 5% of the amount


_AMOUNT_TYPE_LABELS = {
    AmountType.INCLUSIVE: "VAT included",
    AmountType.EXCLUSIVE: "VAT added on top",
    AmountType.NO_TAX: "No VAT",
}


def _likely_vat_type(
    invoice: Invoice, verified: frozenset[str], history: SupplierHistory
) -> tuple[Invoice, list[Issue]]:
    """Decision 84: an empty VAT type the amounts do not prove is filled from other evidence,
    with a warning, which stays until the accountant confirms or changes it."""
    if "amount_type" in verified or vat_type_from_amounts(invoice) is not None:
        return invoice, []
    if total_line(invoice) is not None:
        return invoice, []  # sent as one total line (decision 86): the type does not matter
    unset = invoice.model_copy(update={"amount_type": None})
    likely = likely_vat_type(unset, history.vat_type)
    if likely is None or invoice.amount_type not in (None, likely.amount_type):
        return invoice, []  # nothing likely, or chosen otherwise (lines edited since)
    invoice = invoice.model_copy(update={"amount_type": likely.amount_type})
    note = (
        f"{_AMOUNT_TYPE_LABELS[likely.amount_type]} was chosen because {likely.reason}. "
        "The amounts do not prove it: check it against the document."
    )
    return invoice, [Issue("vat_type_likely", Severity.WARNING, "amount_type", note)]


def _resolved_date(
    session: Session,
    document: Document,
    invoice: Invoice,
    pages: Sequence[PageText],
    verified: frozenset[str],
    history: SupplierHistory,
    *,
    settings: Settings,
    today: date,
) -> tuple[Invoice, list[Issue]]:
    """Decision 85: the document date from every clue. Weighed from the model's own date (its
    last answer), so the note stays until the accountant confirms or changes the date."""
    if "invoice_date" in verified:
        return invoice, []
    model = _model_answer(session, document)
    model_date = model.invoice_date if model else invoice.invoice_date
    limits = DateLimits(
        today=today,
        future_slack_days=settings.future_date_slack_days,
        folder=document.folder_date,
        max_days_before_folder=settings.period_max_days_before,
        photo=photo_date(document.source_path, today),
        folder_year_known=folder_year_known(document.source_path),
    )
    decision = resolve_date(model_date, invoice.evidence.get("invoice_date"), pages, limits,
                            history.date_order)  # fmt: skip
    if decision is None or invoice.invoice_date not in (model_date, decision.value):
        return invoice, []  # the model's date stands, or it was changed since
    other = decision.alternative.isoformat() if decision.alternative else None
    note = f"Date {decision.value:%d %b %Y}: {decision.reason}. Check it against the document."
    return invoice.model_copy(update={"invoice_date": decision.value}), [
        Issue("date_resolved", Severity.WARNING, "invoice_date", note, other)
    ]


def _model_answer(session: Session, document: Document) -> Invoice | None:
    """The model's last answer as extraction saved it, before any review edit."""
    data = session.scalars(
        select(Extraction.data)
        .where(Extraction.document_id == document.id)
        .order_by(Extraction.attempt.desc())
        .limit(1)
    ).first()
    try:
        return Invoice.model_validate(data) if data else None
    except ValidationError:
        return None


def require_number(issues: list[Issue], provider: AccountingProvider) -> list[Issue]:
    """A receipt without a number may be posted (a warning), unless the client's accounting
    system takes no bill without one (Zoho Books): then it is an error, so the accountant types
    the number before approving rather than having the bill sent back (option B, decision 74)."""
    entry = PROVIDERS.get(provider)
    if entry is None or not entry.number_required:
        return issues
    return [
        Issue(
            "receipt_number_needed",
            Severity.ERROR,
            issue.field,
            f"No receipt number found. {entry.name} needs one: type the number printed on the "
            "receipt (for example a booking or trip number).",
        )
        if issue.code == "receipt_number_missing"
        else issue
        for issue in issues
    ]


def _explain_total(
    storage: Storage,
    document: Document,
    invoice: Invoice,
    pages: list[PageText],
    issues: list[Issue],
    settings: Settings,
    context: RuleContext,
) -> list[Issue]:
    """Task 4.13: a total that does not add up because of charges printed outside the lines
    (delivery fee, service fee, discount...) gets one error naming them, with the charges to
    add as lines; when nothing explains it, the totals issues stay as they are. (Decision 49's
    "the subtotal leaves out charges the lines hold" cannot happen since the subtotal is worked
    out from the total: decision 81.)

    The VAT is checked on the total less the VAT and the lines without VAT: a charge printed
    outside the lines without VAT (a tip) makes it look wrong until it is added as a line. When
    counting the charges as such lines puts the VAT right, that VAT error goes too: one error,
    the charges to add."""
    if not any(issue.code in TOTAL_GAP_CODES for issue in issues):
        return issues
    charges = find_charges(invoice, [pages, load_charge_text(storage, document)],
                           settings.charge_hint_words)  # fmt: skip
    if charges is None:
        return issues
    named = " + ".join(f"{c.label} {c.amount:.2f}" for c in charges)
    kind = "charge" if len(charges) == 1 else "charges"
    replaced = TOTAL_GAP_CODES
    untaxed = [Line(description=c.label, amount=c.amount, tax_type=TaxType.NONE) for c in charges]
    with_charges = invoice.model_copy(update={"lines": [*invoice.lines, *untaxed]})
    if not any(i.code in VAT_CODES for i in check_vat(with_charges, context)):
        replaced = replaced | VAT_CODES
    others = [i for i in issues if i.code not in replaced]
    return [
        *others,
        Issue(
            "total_has_charges",
            Severity.ERROR,
            "total",
            f"The total includes {kind} printed outside the lines: {named}. They add up exactly "
            "to the difference: add them as lines.",
            charges=[{"label": c.label, "amount": f"{c.amount:.2f}"} for c in charges],
        ),
    ]


def working_copy(document: Document) -> Invoice | None:
    """The stored invoice, or None if there is none or it no longer validates."""
    if document.invoice is None:
        return None
    try:
        return Invoice.model_validate(document.invoice)
    except ValidationError:  # stored copy no longer valid (e.g. edited badly): review it
        logger.warning("document %s: stored invoice does not validate", document.id)
        return None


def _last_extraction_error(session: Session, document: Document) -> str | None:
    return session.scalars(
        select(Extraction.error)
        .where(Extraction.document_id == document.id)
        .order_by(Extraction.attempt.desc())
        .limit(1)
    ).first()


def _provider(session: Session, document: Document, settings: Settings) -> AccountingProvider:
    """The client's own accounting system (decision 25); the setting is only a fallback."""
    client = session.scalars(select(Client).where(Client.client_id == document.client_id)).first()
    return client.accounting_provider if client else settings.accounting_provider
