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
and its payment is recorded in the accounting system. For a client whose expenses go as
expenses (decision 89; decided at approval, `outbox.sent_as`), an expense is sent as an expense
paid through its account instead: final there, its lines laid out so the VAT the system works
out is the printed VAT. The client's provider comes from the registry (decision 43): nothing
here names one.

A connection whose access is gone for good (decision 68: withdrawn, a permission missing) pauses
the client's rows: nothing is sent, no attempt is counted, until someone connects again. An
access token refused once (401) is renewed and the row tried again.

A posted document deleted or voided in the accounting system comes back to review only when
that system confirms it is gone (decision 76): approved again, it is a new row with a new key.

A row that gives up (DEAD: unknown error, or every attempt used) stays visible as "could not
send" (decision 69): the accountant tries it again with the same key and saved progress, or sends
it back to review, which is refused once the create request may have reached the system.
"""

import logging
import uuid
from collections.abc import Callable, Mapping, MutableMapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import PurePosixPath
from typing import Any

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.orm import Session

from app.adapters.accounting.base import (
    CREATE_SENT,
    Attachment,
    PostingAccessDenied,
    PostingBill,
    PostingExpense,
    PostingLine,
    PostingRefused,
    PostingUnavailable,
)
from app.adapters.accounting.registry import Registry
from app.adapters.storage import Storage
from app.adapters.storage.base import NotFoundError
from app.core.config import Settings
from app.domain.enums import (
    AccountingProvider,
    AmountType,
    DocumentStatus,
    DocumentType,
    ExpensesSentAs,
    LineGrouping,
    TaxType,
)
from app.domain.invoice import Invoice, Line
from app.domain.state_machine import transition
from app.models import AccountingConnection, Client, Contact, Document, Outbox
from app.models.outbox import ACTIVE_OUTBOX, OutboxStatus
from app.pipeline.coding import ChartOfAccounts, load_chart
from app.pipeline.total_line import TotalLine, total_line
from app.pipeline.verify.step import working_copy
from app.services.accounting_connections import (
    AccountingConnectionError,
    ReconnectNeeded,
    access_token,
    expire_access,
    mark_reconnect_needed,
)

logger = logging.getLogger(__name__)

ACTOR = "system:posting"
STALE_AFTER = timedelta(minutes=10)
MAX_ATTEMPTS = 8  # temporary failures; then DEAD, for a human
FIRST_WAIT_SECONDS = 60
MAX_WAIT_SECONDS = 30 * 60
NOTE = "Created by Missan invoice automation after review and approval in the Review Workbench."
TOLERANCE = Decimal("0.01")
MAX_DESCRIPTION = 4000  # the shortest line-description limit of the systems we post to
# Sent as bills (decision 50: a receipt is a bill already paid), or an expense as an expense
# (decision 89).
POSTED_AS_BILLS = frozenset({DocumentType.BILL, DocumentType.RECEIPT})
SENDABLE = (DocumentStatus.APPROVED, DocumentStatus.POSTING)
POSTED_STATUSES = (DocumentStatus.POSTED, DocumentStatus.SYNCED)

Sessions = Callable[[], AbstractContextManager[Session]]


@dataclass(frozen=True, slots=True)
class Outcome:
    outbox_id: str
    result: str  # "posted" | "refused" | "retry" | "paused" | "dead"
    retry_in: int | None = None  # seconds, for "retry"


class SendError(Exception):
    """Try again / send back cannot be done now; the message is safe to show the accountant."""


@dataclass(frozen=True, slots=True)
class Sending:
    """Where one approved document is on its way to the accounting system (decision 69)."""

    state: str  # "waiting" | "sending" | "retrying" | "paused" | "failed" | "posted"
    reason: str | None = None  # why it is retrying, paused or failed
    next_try_at: datetime | None = None  # retrying: when
    can_send_back: bool = False  # failed, and the create request was never sent
    as_expense: bool = False  # sent (or being sent) as an expense, final there (decision 89)


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
    region: str | None
    total: Decimal | None
    unavailable: str | None = None
    paused: str | None = None  # the connection needs a new sign-in: why
    expense: PostingExpense | None = None  # sent as an expense instead of `bill` (decision 89)


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
    if claim.paused is not None:
        return _paused(sessions, claim, claim.paused)
    if claim.unavailable is not None:
        return _unavailable(sessions, claim, PostingUnavailable(claim.unavailable))
    assert (claim.bill or claim.expense) and claim.token and claim.tenant_id  # noqa: S101

    progress = dict(claim.progress)
    try:
        system = registry[claim.provider].system(claim.token, claim.tenant_id, claim.region)
        save = _saver(sessions, claim, progress)
        if claim.expense is not None:
            posted = system.post_expense(claim.expense, key=claim.key, progress=progress, save=save)
        else:
            assert claim.bill  # noqa: S101 - one of the two, set in _claim
            posted = system.post_bill(claim.bill, key=claim.key, progress=progress, save=save)
    except PostingRefused as exc:
        return _refused(sessions, claim, str(exc))
    except PostingAccessDenied as exc:
        return _access_denied(sessions, claim, exc)
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
        if posted.contact_external_id is not None:  # an expense may go without a supplier
            _remember_contact(session, document, claim, posted.contact_external_id)
        transition(session, document, DocumentStatus.POSTED, actor=ACTOR, details={
            "external_id": posted.external_id,
            "contact_created": posted.contact_created,
            "due_date_from": posted.due_date_from,
            "attached": posted.attached,
            "sent_as": ExpensesSentAs.EXPENSE.value if claim.expense else ExpensesSentAs.BILL.value,
            "total_matches": matches,  # flag, never fix (rule 8): their total is not changed
        })  # fmt: skip
        outbox.status, outbox.step, outbox.last_error = OutboxStatus.DONE, "posted", None
    return Outcome(str(claim.outbox_id), "posted")


def sending_of(session: Session, document: Document) -> Sending | None:
    """None: not approved (yet or any more)."""
    return sending_states(session, [document]).get(document.id)


def sending_states(session: Session, documents: Sequence[Document]) -> dict[uuid.UUID, Sending]:
    """Where each approved document is (a queue page at a time): one query for the outbox rows,
    one per client for a paused connection, whatever the number of documents."""
    approved = [d for d in documents if d.status in SENDABLE or d.status in POSTED_STATUSES]
    rows = _latest_rows(session, [d.id for d in approved])
    paused = {c: _paused_because(session, c) for c in {d.client_id for d in approved}}
    now = datetime.now(UTC)
    return {d.id: _state(d, rows.get(d.id), paused[d.client_id], now) for d in approved}


def _latest_rows(session: Session, document_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, Outbox]:
    """Each document's latest outbox row (one query)."""
    rows: dict[uuid.UUID, Outbox] = {}
    for outbox in session.scalars(
        select(Outbox).where(Outbox.document_id.in_(document_ids)).order_by(Outbox.created_at)
    ):
        rows[outbox.document_id] = outbox  # the latest row of each document wins
    return rows


def _state(document: Document, outbox: Outbox | None, paused: str | None, now: datetime) -> Sending:
    as_expense = outbox is not None and outbox.sent_as is ExpensesSentAs.EXPENSE
    return replace(_where(document, outbox, paused, now), as_expense=as_expense)


def _where(document: Document, outbox: Outbox | None, paused: str | None, now: datetime) -> Sending:
    if document.status in POSTED_STATUSES:
        return Sending("posted")
    if outbox is None:
        return Sending("waiting")
    if outbox.status is OutboxStatus.DEAD:
        progress = outbox.progress or {}
        return Sending("failed", outbox.last_error, can_send_back=CREATE_SENT not in progress)
    if paused is not None:
        return Sending("paused", paused)
    if outbox.status is OutboxStatus.IN_PROGRESS:
        return Sending("sending")
    if outbox.last_error and outbox.next_attempt_at > now:
        return Sending("retrying", outbox.last_error, outbox.next_attempt_at)
    return Sending("waiting")


def document_links(
    session: Session, registry: Registry, documents: Sequence[Document]
) -> dict[uuid.UUID, str]:
    """Each posted document in its accounting system's own screens (decision 70)."""
    posted = [d for d in documents if d.external_id]
    rows = _latest_rows(session, [d.id for d in posted])
    links: dict[uuid.UUID, str] = {}
    for client_id in {d.client_id for d in posted}:
        client = session.scalars(select(Client).where(Client.client_id == client_id)).one()
        connection = session.scalars(
            select(AccountingConnection).where(AccountingConnection.client_id == client_id)
        ).first()
        entry = registry.get(connection.provider if connection else client.accounting_provider)
        if entry is None:
            continue
        org_ref = connection.org_ref if connection else None
        for document in (d for d in posted if d.client_id == client_id):
            row = rows.get(document.id)
            as_expense = row is not None and row.sent_as is ExpensesSentAs.EXPENSE
            url_of = entry.expense_url if as_expense else entry.document_url
            if url := url_of(org_ref, document.external_id or ""):
                links[document.id] = url
    return links


def try_again(session: Session, document: Document) -> Outbox:
    """A send that gave up starts again from its saved progress, with the same key: a document
    the system already created is returned, never made twice. The caller commits, then asks the
    worker to send the returned row."""
    outbox = _failed_row(session, document)
    outbox.status, outbox.attempts, outbox.last_error = OutboxStatus.PENDING, 0, None
    outbox.next_attempt_at = datetime.now(UTC)
    session.flush()
    return outbox


def send_back(session: Session, document: Document, *, actor: str) -> None:
    """A send that gave up goes back to review, to change and approve again (a new row, a new
    key). Refused once the create request was sent: the document may exist there already, and a
    new key could make it twice. The caller commits."""
    outbox = _failed_row(session, document)
    if CREATE_SENT in (outbox.progress or {}):
        raise SendError(
            "it may already be in the accounting system (the request to create it was sent): "
            "use Try again, which never creates it twice"
        )
    reason = outbox.last_error or "it could not be sent"
    outbox.status = OutboxStatus.FAILED
    document.failure_reason = f"Sent back after it could not be sent: {reason}"
    if document.status is DocumentStatus.APPROVED:  # gave up before it was ever claimed
        transition(session, document, DocumentStatus.POSTING, actor=ACTOR)
    transition(session, document, DocumentStatus.POST_ERROR, actor=actor, reason=reason)
    transition(session, document, DocumentStatus.IN_REVIEW, actor=actor)
    session.flush()


def reopen_deleted(
    session: Session, settings: Settings, registry: Registry, document: Document, *, actor: str
) -> str:
    """A posted document the accountant deleted (or voided) in the accounting system goes back
    to review, to change and approve again: a new outbox row, a new key (decision 76). Only when
    the system itself says the document is gone there, so it can never be sent twice. Returns
    the system's name. The caller commits."""
    if document.status is not DocumentStatus.POSTED or not document.external_id:
        raise SendError("only a document sent to the accounting system can be sent again")
    connection = session.scalars(
        select(AccountingConnection).where(AccountingConnection.client_id == document.client_id)
    ).first()
    if connection is None:
        raise SendError("this client is not connected to an accounting system: connect it first")
    entry = registry[connection.provider]
    row = _latest_row(session, document.id)
    as_expense = row is not None and row.sent_as is ExpensesSentAs.EXPENSE
    try:
        token = access_token(session, settings, registry, document.client_id)
        system = entry.system(token, connection.tenant_id, connection.region)
        gone = system.is_deleted(document.external_id, expense=as_expense)
    except (AccountingConnectionError, PostingUnavailable) as exc:
        raise SendError(f"{entry.name} could not be asked if it is still there ({exc})") from exc
    if not gone:
        raise SendError(
            f"it is still in {entry.name}: delete or void it there first, then send it again"
        )
    deleted = document.external_id
    document.external_id = None
    document.failure_reason = f"Deleted in {entry.name}: change it if needed and approve again."
    transition(session, document, DocumentStatus.IN_REVIEW, actor=actor,
               reason=f"deleted in {entry.name}", details={"external_id": deleted})  # fmt: skip
    session.flush()
    return entry.name


def _failed_row(session: Session, document: Document) -> Outbox:
    outbox = _latest_row(session, document.id, for_update=True)
    if document.status not in SENDABLE or outbox is None or outbox.status is not OutboxStatus.DEAD:
        raise SendError("only a document that could not be sent can be tried again or sent back")
    return outbox


def _latest_row(
    session: Session, document_id: uuid.UUID, *, for_update: bool = False
) -> Outbox | None:
    query = (
        select(Outbox)
        .where(Outbox.document_id == document_id)
        .order_by(Outbox.created_at.desc())
        .limit(1)
    )
    return session.scalars(query.with_for_update() if for_update else query).first()


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
    paused: int = 0  # bills waiting for someone to connect again (decision 68)
    paused_reason: str | None = None
    failed: int = 0  # gave up: waiting for Try again or Send back (decision 69)
    failed_reason: str | None = None  # the latest one's


def summary(session: Session, client_id: str) -> PostingSummary:
    rows = session.execute(
        select(Document.document_type, Outbox.status, Outbox.last_error, Outbox.next_attempt_at)
        .join(Document, Document.id == Outbox.document_id)
        .where(Outbox.client_id == client_id, Outbox.status.in_(ACTIVE_OUTBOX))
    ).all()
    now = datetime.now(UTC)
    paused_reason = _paused_because(session, client_id)
    ready = retrying = paused = 0
    retry_at: datetime | None = None
    retry_reason: str | None = None
    not_yet: dict[str, int] = {}
    for document_type, status, last_error, next_attempt_at in rows:
        if document_type not in POSTED_AS_BILLS:
            key = document_type.value if document_type else "UNKNOWN"
            not_yet[key] = not_yet.get(key, 0) + 1
        elif paused_reason is not None:
            paused += 1
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
    failed = session.scalars(
        select(Outbox.last_error)
        .join(Document, Document.id == Outbox.document_id)
        .where(
            Outbox.client_id == client_id,
            Outbox.status == OutboxStatus.DEAD,
            Document.status.in_(SENDABLE),
        )
        .order_by(Outbox.updated_at.desc())
    ).all()
    # Sent back = in review again, not sent, with the reason (the Review queue's "Not sent").
    sent_back = session.scalars(
        select(func.count()).where(
            Document.client_id == client_id,
            Document.status == DocumentStatus.IN_REVIEW,
            Document.failure_reason.is_not(None),
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
        paused=paused,
        paused_reason=paused_reason if paused else None,
        failed=len(failed),
        failed_reason=failed[0] if failed else None,
    )


def _paused_because(session: Session, client_id: str) -> str | None:
    """Why nothing can be sent for this client until a person acts; None = it can be sent. A
    client on CSV master data waits for the export (5.2) instead."""
    client = session.scalars(select(Client).where(Client.client_id == client_id)).first()
    if client is None or client.accounting_provider is AccountingProvider.MANUAL:
        return None
    connection = session.scalars(
        select(AccountingConnection).where(
            AccountingConnection.client_id == client_id,
            AccountingConnection.provider == client.accounting_provider,
        )
    ).first()
    if connection is None:
        return "not connected: connect the accounting system again"
    if connection.needs_reconnect:
        return connection.reconnect_reason or "connect the accounting system again"
    return None


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
                AccountingConnection.needs_reconnect.is_(False),  # paused (decision 68)
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
            Document.status.in_(SENDABLE),  # never a document changed since it was approved
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
    bill = expense = None
    if outbox.sent_as is ExpensesSentAs.EXPENSE:
        expense, refusal = _expense(session, document, invoice, chart, provider, name)
        if expense is not None:
            expense = replace(expense, attachment=_original(storage, document))
    else:
        bill, refusal = _bill(
            session, document, invoice, chart, provider, name, outbox.line_grouping
        )
        if bill is not None:
            bill = replace(bill, attachment=_original(storage, document))
    token = tenant_id = region = unavailable = paused = None
    if bill is not None or expense is not None:
        try:
            token = access_token(session, settings, registry, outbox.client_id)
            connection = session.scalars(
                select(AccountingConnection).where(
                    AccountingConnection.client_id == outbox.client_id
                )
            ).one()
            tenant_id, region = connection.tenant_id, connection.region
        except ReconnectNeeded as exc:
            paused = str(exc)  # marked on the connection: waits for a new sign-in
        except AccountingConnectionError as exc:
            unavailable = str(exc)  # e.g. the provider could not be reached to renew access
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
        region=region,
        total=invoice.total if invoice else None,
        unavailable=unavailable,
        paused=paused,
        expense=expense,
    )


def _bill(
    session: Session,
    document: Document,
    invoice: Invoice | None,
    chart: ChartOfAccounts,
    provider: AccountingProvider,
    name: str,
    grouping: LineGrouping,
) -> tuple[PostingBill | None, str | None]:
    """The bill to send, or why it cannot be sent (checked here, before the accounting system
    sees anything)."""
    if invoice is None:
        return None, "the document has no valid data to send"
    whole = total_line(invoice)
    if whole is not None:
        invoice = _as_total_line(invoice, whole)
    problems = []
    if not invoice.vendor_name:
        problems.append("supplier name is missing")
    lines = _posting_lines(invoice, chart, name, problems)
    if problems or invoice.amount_type is None or not invoice.vendor_name:
        return None, "; ".join(problems)
    if grouping is LineGrouping.BY_ITEM:
        rates = {
            code: VAT_RATES.get(tax_type, Decimal(0)) for tax_type, code in chart.tax_codes.items()
        }
        sent = combine_items(lines, rates, invoice.amount_type)
    else:
        sent = group_lines(lines)
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
            lines=sent,
            note=NOTE,
        ),
        None,
    )


def _expense(
    session: Session,
    document: Document,
    invoice: Invoice | None,
    chart: ChartOfAccounts,
    provider: AccountingProvider,
    name: str,
) -> tuple[PostingExpense | None, str | None]:
    """The expense to send (decision 89), or why it cannot be sent. Its lines go as the layout
    whose VAT, worked out line by line as the accounting system does, is the printed VAT: one
    line per account and VAT, else item by item (shops that round VAT per item), else as read.
    None fits: one per account, and the adapter says what VAT it would make."""
    if invoice is None:
        return None, "the document has no valid data to send"
    whole = total_line(invoice)
    if whole is not None:
        invoice = _as_total_line(invoice, whole)
    problems = []
    if invoice.invoice_date is None:
        problems.append("date is missing")
    payable = {account.code for account in chart.payment_accounts()}
    if not invoice.paid_through or invoice.paid_through not in payable:
        problems.append(f"choose the {name} account it was paid through")
    lines = _posting_lines(invoice, chart, name, problems)
    if problems or invoice.amount_type is None or invoice.invoice_date is None:
        return None, "; ".join(problems)
    vat = invoice.vat or Decimal(0)
    rates = {
        code: VAT_RATES.get(tax_type, Decimal(0)) for tax_type, code in chart.tax_codes.items()
    }
    layouts = [group_lines(lines), combine_items(lines, rates, invoice.amount_type), lines]
    sent = next(
        (layout for layout in layouts if _vat(layout, rates, invoice.amount_type) == vat),
        layouts[0],
    )
    return (
        PostingExpense(
            supplier_name=invoice.vendor_name,
            supplier_tax_number=invoice.vendor_trn,
            supplier_external_id=_known_contact(session, document, provider),
            reference=invoice.invoice_number,
            expense_date=invoice.invoice_date,
            amount_type=invoice.amount_type,
            lines=sent,
            paid_through=invoice.paid_through or "",
            vat=vat,
            note=NOTE,
        ),
        None,
    )


def _vat(
    lines: Sequence[PostingLine], rates: Mapping[str, Decimal], amount_type: AmountType
) -> Decimal:
    """The VAT the accounting system works out for these lines: each line rounded to the fils."""
    vats = (_line_vat(line, rates.get(line.tax_code, Decimal(0)), amount_type) for line in lines)
    return sum(vats, Decimal(0))


def _posting_lines(
    invoice: Invoice, chart: ChartOfAccounts, name: str, problems: list[str]
) -> list[PostingLine]:
    """The lines as the accounting system takes them; what stops them is added to `problems`
    (then the lines are not to be sent)."""
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
                    line.description,
                    quantity,
                    unit,
                    line.account_code or "",
                    tax_code or "",
                    line.item_code,
                )
            )
    return lines


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
        names = _joined([m.description for m in members])
        grouped.append(PostingLine(names, Decimal(1), amount, account_code, tax_code))
    return grouped


def _as_total_line(invoice: Invoice, whole: TotalLine) -> Invoice:
    """Decision 86: lines that do not add up are sent as one line giving the printed total and
    VAT, described by the items read."""
    line = Line(
        description=_joined([line.description for line in invoice.lines]),
        amount=whole.amount,
        account_code=whole.account_code,
        tax_type=TaxType.STANDARD_5,
    )
    return invoice.model_copy(update={"amount_type": whole.amount_type, "lines": [line]})


def _joined(descriptions: Sequence[str | None]) -> str | None:
    names = "; ".join(d for d in descriptions if d)
    if len(names) > MAX_DESCRIPTION:
        names = names[: MAX_DESCRIPTION - 1] + "…"
    return names or None


# The VAT the accounting system works out on a line, by our VAT type (UAE: 5% or none).
VAT_RATES = {TaxType.STANDARD_5: Decimal("0.05"), TaxType.REVERSE_CHARGE: Decimal("0.05")}


def combine_items(
    lines: Sequence[PostingLine], rates: Mapping[str, Decimal], amount_type: AmountType
) -> list[PostingLine]:
    """Item by item (decision 73): identical items, the same product, unit price, account and
    VAT code, are one line with their quantities added ("Garam Masala 1 x 8.80" three times:
    3 x 8.80), in the order they first appear. The same product: the same printed barcode
    when there is one (decision 82: two sizes of the same name stay apart, an OCR slip in the
    name does not split one), else the same name (any case and spacing). A line without
    either stays as it is. The accounting system works out the VAT per line, rounded to the fils: a
    group whose VAT that way would differ from its lines' VAT is sent line by line, so the
    posted total is always the approved one."""
    groups: dict[tuple[str, Decimal, str, str], list[PostingLine]] = {}
    for n, line in enumerate(lines):
        name = " ".join((line.description or "").split()).casefold()
        product = f"code:{line.item_code}" if line.item_code else name or f"#{n}"
        key = (product, line.unit_amount, line.account_code, line.tax_code)
        groups.setdefault(key, []).append(line)
    combined = []
    for members in groups.values():
        rate = rates.get(members[0].tax_code, Decimal(0))
        quantity = sum((m.quantity for m in members), Decimal(0))
        one = replace(members[0], quantity=quantity)
        same_vat = _line_vat(one, rate, amount_type) == sum(
            (_line_vat(m, rate, amount_type) for m in members), Decimal(0)
        )
        combined += [one] if len(members) == 1 or same_vat else members
    return combined


def _line_vat(line: PostingLine, rate: Decimal, amount_type: AmountType) -> Decimal:
    amount = line.quantity * line.unit_amount
    if amount_type is AmountType.INCLUSIVE:
        vat = amount * rate / (1 + rate)
    elif amount_type is AmountType.EXCLUSIVE:
        vat = amount * rate
    else:
        vat = Decimal(0)
    return vat.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


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


def _access_denied(sessions: Sessions, claim: _Claim, exc: PostingAccessDenied) -> Outcome:
    """403: only a new sign-in helps, pause. 401: renew the access at the next try."""
    if exc.reconnect:
        with sessions() as session:
            outbox, _ = _load(session, claim.outbox_id)
            mark_reconnect_needed(session, outbox.client_id, str(exc))
        return _paused(sessions, claim, str(exc))
    with sessions() as session:
        outbox, _ = _load(session, claim.outbox_id)
        expire_access(session, outbox.client_id)
    return _unavailable(sessions, claim, exc)


def _paused(sessions: Sessions, claim: _Claim, reason: str) -> Outcome:
    """Back in line, the attempt not counted: it waits for a person, not for the system."""
    with sessions() as session:
        outbox, _ = _load(session, claim.outbox_id)
        outbox.status, outbox.last_error = OutboxStatus.PENDING, reason
        outbox.attempts = max(outbox.attempts - 1, 0)
        outbox.next_attempt_at = datetime.now(UTC)
    logger.info("outbox %s: paused until %s is connected again", claim.outbox_id, claim.name)
    return Outcome(str(claim.outbox_id), "paused")


def _dead(sessions: Sessions, claim: _Claim, reason: str) -> Outcome:
    with sessions() as session:
        outbox, _ = _load(session, claim.outbox_id)
        outbox.status, outbox.last_error = OutboxStatus.DEAD, reason
    return Outcome(str(claim.outbox_id), "dead")


def _remember_contact(
    session: Session, document: Document, claim: _Claim, external_id: str
) -> None:
    """The supplier's contact there, kept in master data so the next bill finds it directly."""
    sent = claim.bill or claim.expense
    assert sent  # noqa: S101 - posted, so there was a bill or an expense
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
            name=sent.supplier_name or "",
            tax_number=sent.supplier_tax_number,
            is_supplier=True,
        )
        session.add(contact)
        session.flush()
    document.contact_id = contact.id
