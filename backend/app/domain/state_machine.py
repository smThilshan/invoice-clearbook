"""Document state machine: the only definition of which status moves are allowed.

`check_transition` is the pure rule. The DB-writing `transition()` (status update + audit_log row)
is added here with the tables in task 1.4. Only APPROVED can move to POSTING; the DB enforces
that one edge again with a trigger.
"""

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

from app.domain.enums import DocumentStatus as S


class InvalidTransitionError(Exception):
    def __init__(self, current: S, target: S) -> None:
        super().__init__(f"status change {current} -> {target} is not allowed")
        self.current = current
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
