"""The real Celery tasks, run inline (eager) against the test DB with fake OCR and model."""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.domain.enums import DocumentStatus as S
from app.domain.state_machine import StaleStatusError
from app.models import AuditLog, Document, Run, RunStatus
from app.workers import tasks
from app.workers.celery_app import celery_app
from tests.integration.factories import make_client, make_run
from tests.integration.test_extract import FakeClient
from tests.read_fixtures import digital_pdf
from tests.test_read_pages import FakeOcr

pytestmark = pytest.mark.integration

Fakes = dict[str, FakeClient]  # {"llm": the model client the tasks use}


@pytest.fixture
def pipeline(session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Fakes]:
    """Wire the tasks to the test session, a temp inbox/store and fakes; run jobs inline."""
    from app.adapters.storage.local import LocalStorage

    settings = Settings(_env_file=None, stable_file_seconds=0)
    make_client(session, tmp_path / "inbox")  # the restaurant's shared-drive folder
    storage = LocalStorage(tmp_path / "store")
    client = {"llm": FakeClient()}

    @contextmanager
    def scope() -> Iterator[Session]:
        with session.begin_nested():
            yield session

    monkeypatch.setattr(tasks, "session_scope", scope)
    monkeypatch.setattr(tasks, "get_settings", lambda: settings)
    monkeypatch.setattr(tasks, "get_storage", lambda _=None: storage)
    monkeypatch.setattr(tasks, "get_ocr_engine", FakeOcr)
    monkeypatch.setattr(tasks, "get_extraction_client", lambda _=None: client["llm"])
    celery_app.conf.task_always_eager = True
    try:
        yield client
    finally:
        celery_app.conf.task_always_eager = False


def drop_invoice(session: Session, tmp_path: Path) -> Run:
    run = make_run(session)
    run.folder = "October 2026/20"  # the folder the user picked
    folder = tmp_path / "inbox/October 2026/20"
    folder.mkdir(parents=True)
    (folder / "inv.pdf").write_bytes(digital_pdf())
    return run


def moves(session: Session, doc: Document) -> list[tuple[S, S]]:
    entries = session.scalars(
        select(AuditLog).where(AuditLog.document_id == doc.id).order_by(AuditLog.id)
    )
    return [(e.from_status, e.to_status) for e in entries]


def only_document(session: Session) -> Document:
    return session.scalars(select(Document)).one()


def test_run_flows_through_to_review(session: Session, tmp_path: Path, pipeline: Fakes) -> None:
    run = drop_invoice(session, tmp_path)

    tasks.scan_run.delay(str(run.id))

    doc = only_document(session)
    assert doc.status is S.IN_REVIEW
    assert doc.queue is not None and doc.issues is not None
    assert doc.invoice is not None and doc.page_count == 1
    assert moves(session, doc) == [
        (S.CAPTURED, S.READ),
        (S.READ, S.EXTRACTED),
        (S.EXTRACTED, S.VERIFIED),
        (S.VERIFIED, S.IN_REVIEW),
    ]
    assert len(pipeline["llm"].requests) == 1


def test_crash_in_a_step_fails_the_document_and_stops_the_chain(
    session: Session, tmp_path: Path, pipeline: Fakes, monkeypatch: pytest.MonkeyPatch
) -> None:
    def crash(*_: object, **__: object) -> None:
        raise RuntimeError("secret invoice text in the message")

    monkeypatch.setattr(tasks.read, "read_document", crash)
    run = drop_invoice(session, tmp_path)

    tasks.scan_run.delay(str(run.id))

    doc = only_document(session)
    assert doc.status is S.FAILED
    assert doc.failure_reason == "read: unexpected error (RuntimeError)"  # no message content
    assert pipeline["llm"].requests == []


def test_model_down_after_all_retries_fails_the_document(
    session: Session, tmp_path: Path, pipeline: Fakes
) -> None:
    run = drop_invoice(session, tmp_path)
    pipeline["llm"] = FakeClient(fail=True)

    tasks.scan_run.delay(str(run.id))  # eager mode runs the retries inline, one after another

    doc = only_document(session)
    assert len(pipeline["llm"].requests) == tasks.MODEL_MAX_RETRIES + 1
    assert doc.status is S.FAILED
    assert doc.failure_reason is not None
    assert doc.failure_reason.startswith("extract: model unavailable after 4 attempts")


def test_superseded_step_stops_quietly(
    session: Session, tmp_path: Path, pipeline: Fakes, monkeypatch: pytest.MonkeyPatch
) -> None:
    def lost_race(*_: object, **__: object) -> None:
        raise StaleStatusError(S.READ, S.EXTRACTED)

    monkeypatch.setattr(tasks.extract, "extract_document", lost_race)
    run = drop_invoice(session, tmp_path)

    tasks.scan_run.delay(str(run.id))

    assert only_document(session).status is S.READ  # not FAILED: another worker owns it


def test_enqueue_next_knows_what_each_status_needs(pipeline: Fakes) -> None:
    assert not tasks.enqueue_next("00000000-0000-0000-0000-000000000000", S.IN_REVIEW)
    assert not tasks.enqueue_next("00000000-0000-0000-0000-000000000000", S.FAILED)


def test_unreachable_folder_fails_the_run_with_a_reason(
    session: Session, tmp_path: Path, pipeline: Fakes
) -> None:
    run = make_run(session)
    run.folder = "December 2030"  # removed, or the shared drive is not mounted

    assert tasks.scan_run.delay(str(run.id)).get() == 0

    assert (run.status, run.files) == (RunStatus.FAILED, [])
    assert run.failure_reason is not None and "folder not found" in run.failure_reason


def test_a_stopped_run_takes_no_further_step(
    session: Session, tmp_path: Path, pipeline: Fakes
) -> None:
    """Task 4.19: stopped while a file waits for the AI, nothing more happens to it; Resume
    continues it from there."""
    from app.models import RunStatus

    run = drop_invoice(session, tmp_path)
    run.status = RunStatus.STOPPED

    tasks.scan_run.delay(str(run.id))  # stopped before the scan: nothing is listed
    assert run.files is None

    run.status = RunStatus.RUNNING
    tasks.scan_run.delay(str(run.id))
    doc = only_document(session)
    doc.status = S.READ  # pretend it was stopped right after reading
    run.status = RunStatus.STOPPED
    session.flush()
    calls_before = len(pipeline["llm"].requests)

    assert tasks.extract_document.delay(str(doc.id)).get() == "stopped"
    assert doc.status is S.READ
    assert len(pipeline["llm"].requests) == calls_before  # the AI was not asked again


def test_a_force_stop_during_the_ai_read_saves_nothing(
    session: Session, tmp_path: Path, pipeline: Fakes
) -> None:
    """Task 4.20: asked while the model reads, the call is abandoned; the document stays READ,
    not failed, its working mark cleared (it shows as stopped); Resume reads it again."""
    from app.adapters.llm import ExtractionStopped

    class ForceStoppedWhileReading:
        model = "fake"

        def extract(self, request: object, *, should_stop: object = None) -> object:
            assert callable(should_stop) and should_stop()  # asked while waiting
            raise ExtractionStopped("stopped by the user")

        def close(self) -> None:
            pass

    run = drop_invoice(session, tmp_path)
    tasks.scan_run.delay(str(run.id))
    doc = only_document(session)
    doc.status = S.READ  # the AI read is about to start
    run.force_stop = True  # asked during that read
    pipeline["llm"] = ForceStoppedWhileReading()  # type: ignore[assignment]
    session.flush()

    assert tasks.extract_document.delay(str(doc.id)).get() == "stopped"
    assert (doc.status, doc.failure_reason, doc.working_step) == (S.READ, None, None)
