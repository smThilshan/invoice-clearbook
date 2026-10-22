"""Step timings (task 4.9): where a document's processing time went, per pipeline step.

Each step saves its own work time in the details of the status change it writes (`work_ms`;
Verify also `coding_ms`, the model's part of it). Each step runs in one transaction that begins
when the task loads the document, and the audit row's `created_at` is the database's
transaction start (`now()`), so it marks when the step started. Waiting is therefore the gap
between one step's end (start + work) and the next one's start: time spent queued behind other
documents, or on model retries.

Documents processed before 4.9 have no `work_ms`; only the model time (kept per extraction)
is known for them, the rest stays None ("not measured").
"""

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.enums import DocumentStatus
from app.models import AuditLog, Document, Extraction

# Status changes written by the pipeline steps; CAPTURED again = sent back to the start (retry).
STEP_STATUSES = (
    DocumentStatus.CAPTURED,
    DocumentStatus.READ,
    DocumentStatus.EXTRACTED,
    DocumentStatus.VERIFIED,
)


@dataclass(frozen=True, slots=True)
class StepEvent:
    status: DocumentStatus
    at: datetime  # when the step that wrote it started (its transaction start)
    details: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class StepTimings:
    """Milliseconds; None = not measured, or the step has not run yet."""

    waiting_ms: int | None  # queued between steps (all steps measured and finished only)
    read_ms: int | None
    extract_ms: int | None  # the model reading the document
    coding_ms: int | None  # the model suggesting account and VAT codes
    checks_ms: int | None
    total_ms: int | None  # captured -> checks done


def step_timings(
    captured_at: datetime, events: Sequence[StepEvent], model_ms: int | None = None
) -> StepTimings:
    """`events` oldest first. `model_ms` (the last extraction's model time) stands in for the
    Extract step on documents processed before the steps timed themselves."""
    start = captured_at
    latest: dict[DocumentStatus, StepEvent] = {}
    for event in events:
        if event.status is DocumentStatus.CAPTURED:  # retried: only the last attempt counts
            start, latest = event.at, {}
        else:
            latest[event.status] = event

    read, extract, verify = (latest.get(s) for s in STEP_STATUSES[1:])
    read_ms, extract_work, verify_ms = (_work(e) for e in (read, extract, verify))
    coding_ms = _ms(verify.details.get("coding_ms")) if verify else None
    checks_ms = verify_ms - coding_ms if verify_ms is not None and coding_ms is not None else None
    extract_ms = extract_work if extract_work is not None else (model_ms if extract else None)

    waiting_ms = total_ms = None
    if (
        read and extract and verify
        and read_ms is not None and extract_work is not None and verify_ms is not None
    ):  # fmt: skip
        waiting_ms = (
            _gap(start, read.at)
            + _gap(_after(read.at, read_ms), extract.at)
            + _gap(_after(extract.at, extract_work), verify.at)
        )
        total_ms = waiting_ms + read_ms + extract_work + verify_ms

    return StepTimings(waiting_ms, read_ms, extract_ms, coding_ms, checks_ms, total_ms)


def load_timings(session: Session, documents: Sequence[Document]) -> dict[uuid.UUID, StepTimings]:
    """Timings of each document, from two queries for all of them."""
    if not documents:
        return {}
    ids = [document.id for document in documents]
    clients = {document.client_id for document in documents}
    events: dict[uuid.UUID, list[StepEvent]] = {document_id: [] for document_id in ids}
    for document_id, status, at, details in session.execute(
        select(AuditLog.document_id, AuditLog.to_status, AuditLog.created_at, AuditLog.details)
        .where(
            AuditLog.client_id.in_(clients),
            AuditLog.document_id.in_(ids),
            AuditLog.to_status.in_(STEP_STATUSES),
        )
        .order_by(AuditLog.id)
    ):
        events[document_id].append(StepEvent(status, at, details or {}))

    model_ms: dict[uuid.UUID, int | None] = {}
    for document_id, duration_ms in session.execute(
        select(Extraction.document_id, Extraction.duration_ms)
        .where(Extraction.client_id.in_(clients), Extraction.document_id.in_(ids))
        .order_by(Extraction.attempt)
    ):
        model_ms[document_id] = duration_ms  # the last attempt wins

    return {d.id: step_timings(d.created_at, events[d.id], model_ms.get(d.id)) for d in documents}


def _work(event: StepEvent | None) -> int | None:
    return _ms(event.details.get("work_ms")) if event else None


def _ms(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _after(at: datetime, ms: int) -> float:
    return at.timestamp() * 1000 + ms


def _gap(earlier: datetime | float, later: datetime) -> int:
    """Milliseconds from `earlier` to `later`, never negative (clocks round to the ms)."""
    begin = earlier.timestamp() * 1000 if isinstance(earlier, datetime) else earlier
    return max(0, round(later.timestamp() * 1000 - begin))
