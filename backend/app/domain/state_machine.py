"""Document state machine: the only definition of which status moves are allowed.

`check_transition` is the pure rule; `transition()` is the only code that changes a document's
status, and it writes the audit_log row in the same transaction. Only APPROVED can move to
POSTING; the DB enforces that one edge again with a trigger (decision 19).
"""

from collections.abc import Mapping
from types import MappingProxyType
from typing import Any, Final

from sqlalchemy import update
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import set_committed_value

from app.domain.enums import DocumentStatus as S
from app.models import AuditLog, Document


class InvalidTransitionError(Exception):
    def __init__(self, current: S, target: S) -> None:
        super().__init__(f"status change {current} -> {target} is not allowed")
        self.current = current
        self.target = target


class StaleStatusError(Exception):
    """The document's status changed in the DB since it was loaded (another worker or user)."""

    def __init__(self, expected: S, target: S) -> None:
        super().__init__(f"status is no longer {expected}; refusing to move to {target}")
        self.expected = expected
        self.target = target


ALLOWED_TRANSITIONS: Final[Mapping[S, frozenset[S]]] = MappingProxyType(
    {
        S.CAPTURED: frozenset({S.READ, S.DUPLICATE, S.FAILED}),  # DUPLICATE: same file hash
        S.READ: frozenset({S.EXTRACTED, S.FAILED}),
        S.EXTRACTED: frozenset({S.VERIFIED, S.FAILED}),
        S.VERIFIED: frozenset({S.IN_REVIEW, S.NON_POSTABLE}),
        S.IN_REVIEW: frozenset({S.APPROVED, S.REJECTED, S.NON_POSTABLE}),
        S.APPROVED: frozenset({S.POSTING}),
        S.POSTING: frozenset({S.POSTED, S.POST_ERROR}),  # retries stay in POSTING
        S.POST_ERROR: frozenset({S.IN_REVIEW}),
        S.POSTED: frozenset({S.SYNCED, S.VOIDED}),
        S.SYNCED: frozenset({S.VOIDED}),
        S.NON_POSTABLE: frozenset({S.IN_REVIEW}),  # accountant un-parks a misclassified doc
        S.FAILED: frozenset({S.CAPTURED}),  # re-run the pipeline
        S.REJECTED: frozenset(),
        S.DUPLICATE: frozenset(),
        S.VOIDED: frozenset(),
    }
)

TERMINAL_STATUSES: Final = frozenset(s for s, targets in ALLOWED_TRANSITIONS.items() if not targets)


def can_transition(current: S, target: S) -> bool:
    return target in ALLOWED_TRANSITIONS[current]


def check_transition(current: S, target: S) -> None:
    if not can_transition(current, target):
        raise InvalidTransitionError(current, target)


def transition(
    session: Session,
    document: Document,
    target: S,
    *,
    actor: str,
    reason: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    """Move `document` to `target` and record it. Runs inside the caller's transaction.

    The UPDATE only matches if the status is still the one we loaded, so two workers racing on
    the same document cannot both win: the loser gets StaleStatusError and nothing is written.
    """
    current = document.status
    check_transition(current, target)

    result = session.execute(
        update(Document)
        .where(Document.id == document.id, Document.status == current)
        .values(status=target)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:  # type: ignore[attr-defined]  # CursorResult for UPDATE
        raise StaleStatusError(current, target)

    # Reflect the change without marking the attribute dirty (no second UPDATE on flush).
    set_committed_value(document, "status", target)
    session.expire(document, ["updated_at"])
    session.add(
        AuditLog(
            client_id=document.client_id,
            document_id=document.id,
            from_status=current,
            to_status=target,
            actor=actor,
            reason=reason,
            details=details or {},
        )
    )
