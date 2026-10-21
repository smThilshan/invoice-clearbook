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
codes to the lines. An unreadable extraction still goes to review (Exception, "enter it by
hand"). If the model is down, coding leaves accounts for review with a warning; verification
itself never needs the model to succeed.
"""

import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.llm import ExtractionClient
from app.adapters.storage import Storage
from app.core.config import Settings
from app.domain.enums import AccountingProvider, DocumentStatus, QueueCategory
from app.domain.invoice import Invoice
from app.domain.state_machine import transition
from app.models import Client, Document, Extraction
from app.pipeline import coding
from app.pipeline.read import load_page_texts
from app.pipeline.verify.duplicates import check_duplicates, claim_keys, vendor_key
from app.pipeline.verify.grounding import check_grounding
from app.pipeline.verify.issues import Issue, Severity
from app.pipeline.verify.rules import RuleContext, run_rules
from app.pipeline.verify.type_check import check_document_type
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
) -> VerifyResult:
    if document.status is not DocumentStatus.EXTRACTED:
        return VerifyResult(VerifyOutcome.SKIPPED)

    provider = _provider(session, document, settings)
    chart = coding.load_chart(session, client_id=document.client_id, provider=provider)
    invoice = working_copy(document)
    coding_issues: list[Issue] = []
    if invoice is not None:
        coded = coding.code_document(
            session,
            invoice,
            chart,
            client,
            client_id=document.client_id,
            vendor_key=vendor_key(invoice),
            min_consistent_approvals=settings.coding_min_consistent_approvals,
        )
        invoice = coded.apply(invoice)
        coding_issues = coded.issues
        document.coding = [line.to_json() for line in coded.lines]

    result = assess(
        session, storage, document, invoice, chart, provider,
        settings=settings, today=today, extra_issues=coding_issues,
    )  # fmt: skip

    summary = {"queue": result.queue.value, "errors": result.errors, "warnings": result.warnings}
    transition(session, document, DocumentStatus.VERIFIED, actor=ACTOR, details=summary)
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
    pages = load_page_texts(storage, document)
    verified = frozenset(document.verified_fields or ())
    context = RuleContext.from_settings(
        settings,
        folder_date=document.folder_date,
        today=today,
        page_text="\n".join(page.text for page in pages),
    )

    error = _last_extraction_error(session, document) if invoice is None else None
    issues = run_rules(invoice, context, extraction_error=error)
    park = False
    if invoice is not None:
        issues += check_grounding(invoice, pages, verified=verified)

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
        document.document_type = invoice.document_type

        types = check_document_type(invoice, pages, confirmed="document_type" in verified)
        issues += types.issues
        park = types.park

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
