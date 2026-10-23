"""Posting approved documents (tasks 5.1, 5.5; architecture §12). Only the posting worker calls
this (rule 11).

One outbox row at a time, in three short transactions so that each saved sub-step survives a
crash:

    1. claim   a due row (FOR UPDATE SKIP LOCKED): IN_PROGRESS, document APPROVED -> POSTING,
               local checks against the client's master data, a working access token
    2. post    the adapter's sub-steps; each result is saved to outbox.progress at once
    3. finish  POSTED with the external ID / back to review with the reason / try again later

A row left IN_PROGRESS by a crashed worker is claimed again after STALE_AFTER and resumes from
its saved progress. Bills only for now (credit notes 5.7, receipts 5.8), and only for clients
connected to Xero (the export, 5.2, comes later): other rows wait.
"""

import logging
import uuid
from collections.abc import Callable, MutableMapping
from contextlib import AbstractContextManager
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import PurePosixPath
from typing import Any

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.orm import Session

from app.adapters.accounting.base import (
    Attachment,
    PostingBill,
    PostingLine,
    PostingRefused,
    PostingSystem,
    PostingUnavailable,
)
from app.adapters.accounting.xero.auth import XeroAuth
from app.adapters.storage import Storage
from app.adapters.storage.base import NotFoundError
from app.core.config import Settings
from app.domain.enums import AccountingProvider, DocumentStatus, DocumentType
from app.domain.invoice import Invoice, Line
from app.domain.state_machine import transition
from app.models import AccountingConnection, Client, Contact, Document, Outbox
from app.models.outbox import ACTIVE_OUTBOX, OutboxStatus
from app.pipeline.coding import ChartOfAccounts, load_chart
from app.pipeline.verify.step import working_copy
from app.services.accounting_connections import AccountingConnectionError, access_token

logger = logging.getLogger(__name__)

ACTOR = "system:posting"
STALE_AFTER = timedelta(minutes=10)
MAX_ATTEMPTS = 8  # temporary failures; then DEAD, for a human
FIRST_WAIT_SECONDS = 60
MAX_WAIT_SECONDS = 30 * 60
NOTE = "Created by Missan invoice automation after review and approval in the Review Workbench."
TOLERANCE = Decimal("0.01")

Sessions = Callable[[], AbstractContextManager[Session]]
SystemFactory = Callable[[str, str], PostingSystem]  # (access token, tenant id) -> adapter


@dataclass(frozen=True, slots=True)
class Outcome:
    outbox_id: str
    result: str  # "posted" | "refused" | "retry" | "dead"
    retry_in: int | None = None  # seconds, for "retry"


@dataclass(frozen=True, slots=True)
class _Claim:
    outbox_id: uuid.UUID
    key: str
    progress: dict[str, Any]
    bill: PostingBill | None
    refusal: str | None
    token: str | None
    tenant_id: str | None
    total: Decimal | None
    unavailable: str | None = None


def send_next(
    sessions: Sessions,
    settings: Settings,
    auth: XeroAuth,
    system: SystemFactory,
    storage: Storage,
    *,
    outbox_id: str | None = None,
    client_id: str | None = None,
) -> Outcome | None:
    """Post the next due row (that one row, or any of the client's, or any). None = none due."""
    with sessions() as session:
        claim = _claim(session, settings, auth, storage, outbox_id=outbox_id, client_id=client_id)
    if claim is None:
        return None
    if claim.refusal is not None:
        return _refused(sessions, claim, claim.refusal)
    if claim.unavailable is not None:
        return _unavailable(sessions, claim, PostingUnavailable(claim.unavailable))
    assert claim.bill and claim.token and claim.tenant_id  # noqa: S101 - set together in _claim

    progress = dict(claim.progress)
    try:
        posted = system(claim.token, claim.tenant_id).post_bill(
            claim.bill, key=claim.key, progress=progress, save=_saver(sessions, claim, progress)
        )
    except PostingRefused as exc:
        return _refused(sessions, claim, str(exc))
    except PostingUnavailable as exc:
        return _unavailable(sessions, claim, exc)
    except Exception as exc:  # unknown: dead-letter, never retried blindly (architecture §12)
        logger.exception("outbox %s: posting failed unexpectedly", claim.outbox_id)
        return _dead(sessions, claim, f"unexpected error ({type(exc).__name__})")

    with sessions() as session:
        outbox, document = _load(session, claim.outbox_id)
        matches = (
            posted.total is None
            or claim.total is None
            or (abs(posted.total - claim.total) <= TOLERANCE)
        )
        document.external_id = posted.external_id
        document.failure_reason = None
        _remember_contact(session, document, claim.bill, posted.contact_external_id)
        transition(session, document, DocumentStatus.POSTED, actor=ACTOR, details={
            "external_id": posted.external_id,
            "contact_created": posted.contact_created,
            "due_date_from": posted.due_date_from,
            "attached": posted.attached,
            "total_matches": matches,  # flag, never fix (rule 8): Xero's total is not changed
        })  # fmt: skip
        outbox.status, outbox.step, outbox.last_error = OutboxStatus.DONE, "posted", None
    return Outcome(str(claim.outbox_id), "posted")


def waiting(session: Session, client_id: str) -> int:
    """Approved documents of this client not posted yet."""
    return session.scalars(
        select(func.count()).where(Outbox.client_id == client_id, Outbox.status.in_(ACTIVE_OUTBOX))
    ).one()


# --- 1. claim --------------------------------------------------------------------------------


def _claim(
    session: Session,
    settings: Settings,
    auth: XeroAuth,
    storage: Storage,
    *,
    outbox_id: str | None,
    client_id: str | None,
) -> _Claim | None:
    now = datetime.now(UTC)
    query = (
        select(Outbox)
        .join(Document, Document.id == Outbox.document_id)
        .join(Client, Client.client_id == Outbox.client_id)
        .join(
            AccountingConnection,
            and_(
                AccountingConnection.client_id == Outbox.client_id,
                AccountingConnection.provider == AccountingProvider.XERO,
            ),
        )
        .where(
            or_(
                and_(Outbox.status == OutboxStatus.PENDING, Outbox.next_attempt_at <= now),
                and_(
                    Outbox.status == OutboxStatus.IN_PROGRESS,
                    Outbox.updated_at < now - STALE_AFTER,
                ),
            ),
            Document.document_type == DocumentType.BILL,
            Client.accounting_provider == AccountingProvider.XERO,
        )
        .order_by(Outbox.created_at)
        .limit(1)
        .with_for_update(of=Outbox, skip_locked=True)
    )
    if outbox_id:
        query = query.where(Outbox.id == uuid.UUID(outbox_id))
    if client_id:
        query = query.where(Outbox.client_id == client_id)
    outbox = session.scalars(query).first()
    if outbox is None:
        return None
    document = session.get_one(Document, outbox.document_id)

    outbox.status, outbox.attempts = OutboxStatus.IN_PROGRESS, outbox.attempts + 1
    if document.status is DocumentStatus.APPROVED:
        transition(session, document, DocumentStatus.POSTING, actor=ACTOR)

    invoice = working_copy(document)
    chart = load_chart(session, client_id=outbox.client_id, provider=AccountingProvider.XERO)
    bill, refusal = _bill(session, document, invoice, chart)
    if bill is not None:
        bill = replace(bill, attachment=_original(storage, document))
    token = tenant_id = unavailable = None
    if bill is not None:
        try:
            token = access_token(session, settings, auth, outbox.client_id)
            connection = session.scalars(
                select(AccountingConnection).where(
                    AccountingConnection.client_id == outbox.client_id
                )
            ).one()
            tenant_id = connection.tenant_id
        except AccountingConnectionError as exc:
            unavailable = str(exc)  # posting waits until Xero is connected again
    session.flush()
    return _Claim(
        outbox_id=outbox.id,
        key=str(outbox.id),  # the outbox row's id is the idempotency key
        progress=dict(outbox.progress or {}),
        bill=bill,
        refusal=refusal,
        token=token,
        tenant_id=tenant_id,
        total=invoice.total if invoice else None,
        unavailable=unavailable,
    )


def _bill(
    session: Session, document: Document, invoice: Invoice | None, chart: ChartOfAccounts
) -> tuple[PostingBill | None, str | None]:
    """The bill to send, or why it cannot be sent (checked here, before Xero sees anything)."""
    if invoice is None:
        return None, "the document has no valid data to send"
    problems = []
    if not invoice.vendor_name:
        problems.append("supplier name is missing")
    if invoice.amount_type is None:
        problems.append("VAT type (included / added / no VAT) is missing")
    if not invoice.lines:
        problems.append("there are no lines")
    lines = []
    for number, line in enumerate(invoice.lines, start=1):
        if not line.account_code or line.account_code not in chart.accounts:
            problems.append(
                f"line {number}: account {line.account_code or '(none)'} is not in Xero's chart "
                "of accounts: choose another"
            )
        tax_code = chart.tax_codes.get(line.tax_type) if line.tax_type else None
        if tax_code is None:
            problems.append(f"line {number}: no Xero VAT rate for this line's VAT type")
        if not problems:
            quantity, unit = _quantity_and_unit(line)
            lines.append(
                PostingLine(
                    line.description, quantity, unit, line.account_code or "", tax_code or ""
                )
            )
    if problems or invoice.amount_type is None or not invoice.vendor_name:
        return None, "; ".join(problems)
    return (
        PostingBill(
            supplier_name=invoice.vendor_name,
            supplier_tax_number=invoice.vendor_trn,
            supplier_external_id=_known_contact(session, document),
            invoice_number=invoice.invoice_number,
            invoice_date=invoice.invoice_date,
            due_date=invoice.due_date,
            currency=invoice.currency,
            amount_type=invoice.amount_type,
            lines=lines,
            note=NOTE,
        ),
        None,
    )


def _original(storage: Storage, document: Document) -> Attachment | None:
    """The original file as captured (decision 41); None if it cannot be read: the bill is
    still posted, and its note says the file is in Missan's folders."""
    try:
        content = storage.get(document.storage_key)
    except NotFoundError:
        logger.warning("document %s: original not found in storage", document.id)
        return None
    name = PurePosixPath(document.source_path).name or f"{document.id}"
    return Attachment(name, document.mime_type, content)


def _quantity_and_unit(line: Line) -> tuple[Decimal, Decimal]:
    """Quantity x unit price when they give exactly the line amount; else 1 x the amount, so
    the line in Xero is always the amount the accountant approved."""
    if (
        line.qty is not None
        and line.unit_price is not None
        and (line.qty * line.unit_price == line.amount)
    ):
        return line.qty, line.unit_price
    return Decimal(1), line.amount


def _known_contact(session: Session, document: Document) -> str | None:
    if document.contact_id is None:
        return None
    contact = session.get(Contact, document.contact_id)
    if contact is None or contact.provider is not AccountingProvider.XERO:
        return None
    return contact.external_id


# --- 2. post ---------------------------------------------------------------------------------


def _saver(
    sessions: Sessions, claim: _Claim, progress: MutableMapping[str, Any]
) -> Callable[[str], None]:
    def save(step: str) -> None:
        with sessions() as session:
            session.execute(
                update(Outbox)
                .where(Outbox.id == claim.outbox_id)
                .values(step=step, progress=dict(progress))
            )

    return save


# --- 3. finish -------------------------------------------------------------------------------


def _load(session: Session, outbox_id: uuid.UUID) -> tuple[Outbox, Document]:
    outbox = session.get_one(Outbox, outbox_id, with_for_update=True)
    return outbox, session.get_one(Document, outbox.document_id)


def _refused(sessions: Sessions, claim: _Claim, reason: str) -> Outcome:
    """Back to review with the reason; approving again makes a new outbox row."""
    with sessions() as session:
        outbox, document = _load(session, claim.outbox_id)
        outbox.status, outbox.last_error = OutboxStatus.FAILED, reason
        document.failure_reason = f"Not sent to Xero: {reason}"
        transition(session, document, DocumentStatus.POST_ERROR, actor=ACTOR, reason=reason)
        transition(session, document, DocumentStatus.IN_REVIEW, actor=ACTOR)
    logger.info("outbox %s: refused, document back to review", claim.outbox_id)
    return Outcome(str(claim.outbox_id), "refused")


def _unavailable(sessions: Sessions, claim: _Claim, exc: PostingUnavailable) -> Outcome:
    with sessions() as session:
        outbox, _ = _load(session, claim.outbox_id)
        if outbox.attempts >= MAX_ATTEMPTS:
            outbox.status, outbox.last_error = OutboxStatus.DEAD, str(exc)
            logger.warning("outbox %s: gave up after %d attempts", claim.outbox_id, outbox.attempts)
            return Outcome(str(claim.outbox_id), "dead")
        wait = exc.retry_after or min(
            FIRST_WAIT_SECONDS * 2 ** (outbox.attempts - 1), MAX_WAIT_SECONDS
        )
        outbox.status, outbox.last_error = OutboxStatus.PENDING, str(exc)
        outbox.next_attempt_at = datetime.now(UTC) + timedelta(seconds=wait)
    logger.info("outbox %s: Xero unavailable, next try in %d s", claim.outbox_id, wait)
    return Outcome(str(claim.outbox_id), "retry", wait)


def _dead(sessions: Sessions, claim: _Claim, reason: str) -> Outcome:
    with sessions() as session:
        outbox, _ = _load(session, claim.outbox_id)
        outbox.status, outbox.last_error = OutboxStatus.DEAD, reason
    return Outcome(str(claim.outbox_id), "dead")


def _remember_contact(
    session: Session, document: Document, bill: PostingBill, external_id: str
) -> None:
    """The supplier's Xero contact, kept in master data so the next bill finds it directly."""
    contact = session.scalars(
        select(Contact).where(
            Contact.client_id == document.client_id,
            Contact.provider == AccountingProvider.XERO,
            Contact.external_id == external_id,
        )
    ).first()
    if contact is None:
        contact = Contact(
            client_id=document.client_id,
            provider=AccountingProvider.XERO,
            external_id=external_id,
            name=bill.supplier_name,
            tax_number=bill.supplier_tax_number,
            is_supplier=True,
        )
        session.add(contact)
        session.flush()
    document.contact_id = contact.id
