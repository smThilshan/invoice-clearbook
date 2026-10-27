"""Posting approved documents (tasks 5.1, 5.5; architecture §12). Only the posting worker calls
this (rule 11).

One outbox row at a time, in three short transactions so that each saved sub-step survives a
crash:

    1. claim   a due row (FOR UPDATE SKIP LOCKED): IN_PROGRESS, document APPROVED -> POSTING,
               local checks against the client's master data, a working access token
    2. post    the adapter's sub-steps; each result is saved to outbox.progress at once
    3. finish  POSTED with the external ID / back to review with the reason / try again later

A row left IN_PROGRESS by a crashed worker is claimed again after STALE_AFTER and resumes from
its saved progress. Bills and receipts for now (credit notes 5.7), and only for clients
connected to an accounting system (the export, 5.2, comes later): other rows wait. A receipt is
posted as a bill, due on its own date (decision 50): it stays Awaiting Approval like every bill,
and its payment is recorded in the accounting system. The client's
provider comes from the registry (decision 43): nothing here names one.
"""

import logging
import uuid
from collections.abc import Callable, MutableMapping, Sequence
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
    PostingUnavailable,
)
from app.adapters.accounting.registry import Registry
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
MAX_DESCRIPTION = 4000  # the shortest line-description limit of the systems we post to
# Sent as bills (decision 50): a receipt is a bill already paid.
POSTED_AS_BILLS = frozenset({DocumentType.BILL, DocumentType.RECEIPT})

Sessions = Callable[[], AbstractContextManager[Session]]


@dataclass(frozen=True, slots=True)
class Outcome:
    outbox_id: str
    result: str  # "posted" | "refused" | "retry" | "dead"
    retry_in: int | None = None  # seconds, for "retry"


@dataclass(frozen=True, slots=True)
class _Claim:
    outbox_id: uuid.UUID
    provider: AccountingProvider
    name: str  # the accounting system's name, for messages
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
    registry: Registry,
    storage: Storage,
    *,
    outbox_id: str | None = None,
    client_id: str | None = None,
) -> Outcome | None:
    """Post the next due row (that one row, or any of the client's, or any). None = none due."""
    with sessions() as session:
        claim = _claim(
            session, settings, registry, storage, outbox_id=outbox_id, client_id=client_id
        )
    if claim is None:
        return None
    if claim.refusal is not None:
        return _refused(sessions, claim, claim.refusal)
    if claim.unavailable is not None:
        return _unavailable(sessions, claim, PostingUnavailable(claim.unavailable))
    assert claim.bill and claim.token and claim.tenant_id  # noqa: S101 - set together in _claim

    progress = dict(claim.progress)
    try:
        posted = (
            registry[claim.provider]
            .system(claim.token, claim.tenant_id)
            .post_bill(
                claim.bill, key=claim.key, progress=progress, save=_saver(sessions, claim, progress)
            )
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
        _remember_contact(session, document, claim, posted.contact_external_id)
        transition(session, document, DocumentStatus.POSTED, actor=ACTOR, details={
            "external_id": posted.external_id,
            "contact_created": posted.contact_created,
            "due_date_from": posted.due_date_from,
            "attached": posted.attached,
            "total_matches": matches,  # flag, never fix (rule 8): their total is not changed
        })  # fmt: skip
        outbox.status, outbox.step, outbox.last_error = OutboxStatus.DONE, "posted", None
    return Outcome(str(claim.outbox_id), "posted")


def waiting(session: Session, client_id: str) -> int:
    """Approved documents of this client not posted yet."""
    return session.scalars(
        select(func.count()).where(Outbox.client_id == client_id, Outbox.status.in_(ACTIVE_OUTBOX))
    ).one()


@dataclass(frozen=True, slots=True)
class PostingSummary:
    """Where a client's approved documents are, so the screen can say what is happening, whether
    it is done, and why (task 5.1)."""

    ready: int  # bills to send now (or being sent)
    retrying: int  # bills waiting to try again after a temporary failure
    retry_at: datetime | None  # the earliest next try
    retry_reason: str | None  # the latest temporary failure, e.g. a rate limit
    not_yet: dict[str, int]  # document type -> count: approved, but not sent in this version
    sent_back: int  # back in review because they could not be sent
    posted: int  # in the accounting system


def summary(session: Session, client_id: str) -> PostingSummary:
    rows = session.execute(
        select(Document.document_type, Outbox.status, Outbox.last_error, Outbox.next_attempt_at)
        .join(Document, Document.id == Outbox.document_id)
        .where(Outbox.client_id == client_id, Outbox.status.in_(ACTIVE_OUTBOX))
    ).all()
    now = datetime.now(UTC)
    ready = retrying = 0
    retry_at: datetime | None = None
    retry_reason: str | None = None
    not_yet: dict[str, int] = {}
    for document_type, status, last_error, next_attempt_at in rows:
        if document_type not in POSTED_AS_BILLS:
            key = document_type.value if document_type else "UNKNOWN"
            not_yet[key] = not_yet.get(key, 0) + 1
        elif status is OutboxStatus.PENDING and last_error and next_attempt_at > now:
            retrying += 1
            if retry_at is None or next_attempt_at < retry_at:
                retry_at, retry_reason = next_attempt_at, last_error
        else:
            ready += 1
    posted = session.scalars(
        select(func.count()).where(
            Document.client_id == client_id, Document.status == DocumentStatus.POSTED
        )
    ).one()
    # Sent back = in review again after a send attempt that could not go through.
    sent_back = session.scalars(
        select(func.count(func.distinct(Document.id)))
        .join(Outbox, Outbox.document_id == Document.id)
        .where(
            Document.client_id == client_id,
            Document.status == DocumentStatus.IN_REVIEW,
            Outbox.status == OutboxStatus.FAILED,
        )
    ).one()
    return PostingSummary(
        ready=ready,
        retrying=retrying,
        retry_at=retry_at,
        retry_reason=retry_reason,
        not_yet=not_yet,
        sent_back=sent_back,
        posted=posted,
    )


# --- 1. claim --------------------------------------------------------------------------------


def _claim(
    session: Session,
    settings: Settings,
    registry: Registry,
    storage: Storage,
    *,
    outbox_id: str | None,
    client_id: str | None,
) -> _Claim | None:
    now = datetime.now(UTC)
    query = (
        select(Outbox, Client.accounting_provider)
        .join(Document, Document.id == Outbox.document_id)
        .join(Client, Client.client_id == Outbox.client_id)
        .join(
            AccountingConnection,
            and_(
                AccountingConnection.client_id == Outbox.client_id,
                AccountingConnection.provider == Client.accounting_provider,
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
            Document.document_type.in_(POSTED_AS_BILLS),
            Client.accounting_provider.in_(list(registry)),
        )
        .order_by(Outbox.created_at)
        .limit(1)
        .with_for_update(of=Outbox, skip_locked=True)
    )
    if outbox_id:
        query = query.where(Outbox.id == uuid.UUID(outbox_id))
    if client_id:
        query = query.where(Outbox.client_id == client_id)
    found = session.execute(query).first()
    if found is None:
        return None
    outbox, provider = found
    name = registry[provider].name
    document = session.get_one(Document, outbox.document_id)

    outbox.status, outbox.attempts = OutboxStatus.IN_PROGRESS, outbox.attempts + 1
    if document.status is DocumentStatus.APPROVED:
        transition(session, document, DocumentStatus.POSTING, actor=ACTOR)

    invoice = working_copy(document)
    chart = load_chart(session, client_id=outbox.client_id, provider=provider)
    bill, refusal = _bill(session, document, invoice, chart, provider, name)
    if bill is not None:
        bill = replace(bill, attachment=_original(storage, document))
    token = tenant_id = unavailable = None
    if bill is not None:
        try:
            token = access_token(session, settings, registry, outbox.client_id)
            connection = session.scalars(
                select(AccountingConnection).where(
                    AccountingConnection.client_id == outbox.client_id
                )
            ).one()
            tenant_id = connection.tenant_id
        except AccountingConnectionError as exc:
            unavailable = str(exc)  # posting waits until it is connected again
    session.flush()
    return _Claim(
        outbox_id=outbox.id,
        provider=provider,
        name=name,
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
    session: Session,
    document: Document,
    invoice: Invoice | None,
    chart: ChartOfAccounts,
    provider: AccountingProvider,
    name: str,
) -> tuple[PostingBill | None, str | None]:
    """The bill to send, or why it cannot be sent (checked here, before the accounting system
    sees anything)."""
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
                f"line {number}: account {line.account_code or '(none)'} is not in {name}'s "
                "chart of accounts: choose another"
            )
        tax_code = chart.tax_codes.get(line.tax_type) if line.tax_type else None
        if tax_code is None:
            problems.append(f"line {number}: no {name} VAT rate for this line's VAT type")
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
            supplier_external_id=_known_contact(session, document, provider),
            invoice_number=invoice.invoice_number,
            invoice_date=invoice.invoice_date,
            # A receipt is paid already: due on its own date (decision 50).
            due_date=invoice.due_date
            or (invoice.invoice_date if invoice.document_type is DocumentType.RECEIPT else None),
            currency=invoice.currency,
            amount_type=invoice.amount_type,
            lines=group_lines(lines),
            note=NOTE,
        ),
        None,
    )


def group_lines(lines: Sequence[PostingLine]) -> list[PostingLine]:
    """One line per account and VAT code (decision 51), in the order they first appear: fees
    and items bought together go to the same account, as accountants enter them. A group of one
    line is sent as it is; a larger group is 1 x the exact sum, described by its items. Our own
    item lines are kept for the checks; only what is sent is grouped."""
    groups: dict[tuple[str, str], list[PostingLine]] = {}
    for line in lines:
        groups.setdefault((line.account_code, line.tax_code), []).append(line)
    grouped = []
    for (account_code, tax_code), members in groups.items():
        if len(members) == 1:
            grouped.append(members[0])
            continue
        amount = sum((m.quantity * m.unit_amount for m in members), Decimal(0))
        names = "; ".join(m.description for m in members if m.description)
        if len(names) > MAX_DESCRIPTION:
            names = names[: MAX_DESCRIPTION - 1] + "…"
        grouped.append(PostingLine(names or None, Decimal(1), amount, account_code, tax_code))
    return grouped


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
    the line sent is always the amount the accountant approved."""
    if (
        line.qty is not None
        and line.unit_price is not None
        and (line.qty * line.unit_price == line.amount)
    ):
        return line.qty, line.unit_price
    return Decimal(1), line.amount


def _known_contact(
    session: Session, document: Document, provider: AccountingProvider
) -> str | None:
    if document.contact_id is None:
        return None
    contact = session.get(Contact, document.contact_id)
    if contact is None or contact.provider is not provider:
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
        document.failure_reason = f"Not sent to {claim.name}: {reason}"
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
    logger.info("outbox %s: %s unavailable, next try in %d s", claim.outbox_id, claim.name, wait)
    return Outcome(str(claim.outbox_id), "retry", wait)


def _dead(sessions: Sessions, claim: _Claim, reason: str) -> Outcome:
    with sessions() as session:
        outbox, _ = _load(session, claim.outbox_id)
        outbox.status, outbox.last_error = OutboxStatus.DEAD, reason
    return Outcome(str(claim.outbox_id), "dead")


def _remember_contact(
    session: Session, document: Document, claim: _Claim, external_id: str
) -> None:
    """The supplier's contact there, kept in master data so the next bill finds it directly."""
    assert claim.bill  # noqa: S101 - posted, so there was a bill
    bill = claim.bill
    contact = session.scalars(
        select(Contact).where(
            Contact.client_id == document.client_id,
            Contact.provider == claim.provider,
            Contact.external_id == external_id,
        )
    ).first()
    if contact is None:
        contact = Contact(
            client_id=document.client_id,
            provider=claim.provider,
            external_id=external_id,
            name=bill.supplier_name,
            tax_number=bill.supplier_tax_number,
            is_supplier=True,
        )
        session.add(contact)
        session.flush()
    document.contact_id = contact.id
