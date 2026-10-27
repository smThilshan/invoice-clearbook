import itertools

import pytest

from app.domain.enums import DocumentStatus as S
from app.domain.state_machine import (
    ALLOWED_TRANSITIONS,
    TERMINAL_STATUSES,
    InvalidTransitionError,
    can_transition,
    check_transition,
)

ALLOWED = {
    (current, target) for current, targets in ALLOWED_TRANSITIONS.items() for target in targets
}
ALL_PAIRS = set(itertools.product(S, S))


def test_every_status_has_an_entry() -> None:
    assert set(ALLOWED_TRANSITIONS) == set(S)


@pytest.mark.parametrize(("current", "target"), sorted(ALLOWED))
def test_allowed_transitions_pass(current: S, target: S) -> None:
    check_transition(current, target)


@pytest.mark.parametrize(("current", "target"), sorted(ALL_PAIRS - ALLOWED))
def test_every_other_transition_raises(current: S, target: S) -> None:
    with pytest.raises(InvalidTransitionError) as excinfo:
        check_transition(current, target)

    assert (excinfo.value.current, excinfo.value.target) == (current, target)


def test_only_approved_can_move_to_posting() -> None:
    assert {current for current in S if can_transition(current, S.POSTING)} == {S.APPROVED}


def test_same_status_is_never_a_transition() -> None:
    assert not any(can_transition(status, status) for status in S)


def test_happy_path() -> None:
    path = [
        S.CAPTURED, S.READ, S.EXTRACTED, S.VERIFIED, S.IN_REVIEW,
        S.APPROVED, S.POSTING, S.POSTED, S.SYNCED,
    ]  # fmt: skip
    for current, target in itertools.pairwise(path):
        check_transition(current, target)


def test_post_error_goes_back_to_review_not_straight_to_posting() -> None:
    assert ALLOWED_TRANSITIONS[S.POST_ERROR] == {S.IN_REVIEW}


def test_terminal_statuses() -> None:
    assert {S.DUPLICATE, S.VOIDED} == TERMINAL_STATUSES


def test_a_rejected_document_can_only_come_back_to_review() -> None:
    assert ALLOWED_TRANSITIONS[S.REJECTED] == {S.IN_REVIEW}  # decision 45


def test_posted_cannot_be_reopened_for_review() -> None:
    assert not can_transition(S.POSTED, S.IN_REVIEW)
    assert not can_transition(S.SYNCED, S.IN_REVIEW)


def test_only_documents_waiting_in_review_can_go_back_to_the_model() -> None:
    """Decision 55: extract again after an app or model update; never once approved."""
    assert {s for s in S if can_transition(s, S.READ)} == {S.CAPTURED, S.IN_REVIEW, S.NON_POSTABLE}
