"""Step timings (task 4.9): work per step from the audit details, waiting from the gaps."""

from datetime import UTC, datetime, timedelta

from app.domain.enums import DocumentStatus
from app.services.timings import StepEvent, step_timings

T0 = datetime(2026, 10, 22, 8, 0, tzinfo=UTC)


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def event(status: DocumentStatus, start_s: float, **details: object) -> StepEvent:
    return StepEvent(status, at(start_s), details)


def finished(**overrides: object) -> list[StepEvent]:
    """Captured at 0 s; read starts at 10 s (6 s work); extract at 30 s (200 s); verify at
    240 s (16 s, of which coding 15 s)."""
    verify: dict[str, object] = {"work_ms": 16_000, "coding_ms": 15_000, **overrides}
    return [
        event(DocumentStatus.READ, 10, work_ms=6_000),
        event(DocumentStatus.EXTRACTED, 30, work_ms=200_000),
        event(DocumentStatus.VERIFIED, 240, **verify),
    ]


def test_each_step_and_the_waiting_between_them() -> None:
    timings = step_timings(T0, finished())

    assert timings.read_ms == 6_000
    assert timings.extract_ms == 200_000
    assert timings.coding_ms == 15_000
    assert timings.checks_ms == 1_000
    # waiting: 10 s before read + 14 s before extract (16 -> 30) + 10 s before verify (230 -> 240)
    assert timings.waiting_ms == 34_000
    assert timings.total_ms == 256_000  # captured -> checks done


def test_in_progress_shows_finished_steps_only() -> None:
    timings = step_timings(T0, finished()[:1])

    assert timings.read_ms == 6_000
    assert timings.extract_ms is None
    assert timings.coding_ms is None
    assert timings.waiting_ms is None
    assert timings.total_ms is None


def test_before_steps_timed_themselves_only_the_model_time_is_known() -> None:
    old = [
        event(DocumentStatus.READ, 10, pages=1),
        event(DocumentStatus.EXTRACTED, 30, attempt=1),
        event(DocumentStatus.VERIFIED, 240, queue="CHECK"),
    ]
    timings = step_timings(T0, old, model_ms=195_000)

    assert timings.extract_ms == 195_000
    assert (timings.read_ms, timings.coding_ms, timings.checks_ms) == (None, None, None)
    assert (timings.waiting_ms, timings.total_ms) == (None, None)


def test_model_time_is_not_shown_before_the_extract_step_ran() -> None:
    assert step_timings(T0, [], model_ms=195_000).extract_ms is None


def test_a_retry_counts_only_the_last_attempt() -> None:
    first = [
        event(DocumentStatus.READ, 5, work_ms=99_000),
        event(DocumentStatus.FAILED, 6),
        event(DocumentStatus.CAPTURED, 1_000),  # sent back to the start
    ]
    later = [StepEvent(e.status, e.at + timedelta(seconds=1_000), e.details) for e in finished()]

    timings = step_timings(T0, [*first, *later])

    assert timings.read_ms == 6_000
    assert timings.total_ms == 256_000


def test_waiting_is_never_negative() -> None:
    steps = [
        event(DocumentStatus.READ, 0, work_ms=6_000),
        event(DocumentStatus.EXTRACTED, 5.999, work_ms=200_000),  # clocks round to the ms
        event(DocumentStatus.VERIFIED, 205.998, work_ms=16_000, coding_ms=15_000),
    ]
    assert step_timings(T0, steps).waiting_ms == 0


def test_values_that_are_not_whole_milliseconds_are_not_measured() -> None:
    timings = step_timings(T0, finished(coding_ms="15000"))

    assert timings.coding_ms is None
    assert timings.checks_ms is None
    assert timings.total_ms == 256_000  # the step's own work time is still known
