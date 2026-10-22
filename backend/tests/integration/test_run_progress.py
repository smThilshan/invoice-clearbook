from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.db import get_session
from app.domain.enums import DocumentStatus as S
from app.main import create_app
from app.models import AuditLog, Run, RunStatus
from app.services.runs import QUEUED, RunState, run_progress
from app.services.timings import StepTimings
from tests.integration.factories import make_client, make_document, make_run

pytestmark = pytest.mark.integration

FOLDER = "Purchase/2026-10-20"


def scanned(session: Session, *names: str) -> Run:
    run = make_run(session)
    run.files = [f"{FOLDER}/{name}" for name in names]
    run.files_found = len(names)
    session.flush()
    return run


def doc(session: Session, run: Run, name: str, status: S, **fields: object) -> None:
    make_document(session, run_id=run.id, source_path=f"{FOLDER}/{name}", status=status, **fields)


def test_not_scanned_yet_is_scanning(session: Session) -> None:
    progress = run_progress(session, make_run(session))

    assert (progress.state, progress.files) == (RunState.SCANNING, [])


def test_queued_and_in_between_files_mean_processing(session: Session) -> None:
    run = scanned(session, "a.pdf", "b.pdf", "c.pdf")
    doc(session, run, "a.pdf", S.IN_REVIEW)
    doc(session, run, "b.pdf", S.READ)

    progress = run_progress(session, run)

    assert progress.state is RunState.PROCESSING
    assert progress.counts == {"IN_REVIEW": 1, "READ": 1, QUEUED: 1}
    assert [f.status_label for f in progress.files] == ["IN_REVIEW", "READ", QUEUED]


def test_finished_when_every_file_settled(session: Session) -> None:
    run = scanned(session, "a.pdf", "b.pdf", "c.pdf")
    doc(session, run, "a.pdf", S.IN_REVIEW)
    doc(session, run, "b.pdf", S.FAILED, failure_reason="read: cannot open PDF")
    doc(session, run, "c.pdf", S.DUPLICATE)

    progress = run_progress(session, run)

    assert progress.state is RunState.FINISHED
    failed = progress.files[1]
    assert (failed.status, failed.failure_reason) == (S.FAILED, "read: cannot open PDF")


def test_file_processed_in_an_earlier_run_counts_as_settled(session: Session) -> None:
    first = scanned(session, "a.pdf")
    doc(session, first, "a.pdf", S.IN_REVIEW)
    second = scanned(session, "a.pdf")  # Run clicked again: ingest says "already seen"

    progress = run_progress(session, second)

    assert progress.state is RunState.FINISHED
    [file] = progress.files
    assert (file.status, file.from_earlier_run) == (S.IN_REVIEW, True)


# --- GET /runs/{id} ---------------------------------------------------------------------


@pytest.fixture
def client(session: Session, tmp_path: Path) -> Iterator[TestClient]:
    make_client(session, tmp_path / "Cafe Al Noor")
    app = create_app(Settings(_env_file=None, app_env="test"))
    app.dependency_overrides[get_session] = lambda: session
    with TestClient(app) as test_client:
        yield test_client


def test_get_run_returns_progress_per_file(client: TestClient, session: Session) -> None:
    run = scanned(session, "inv 1.pdf", "inv 2.pdf")
    run.folder = FOLDER
    doc(session, run, "inv 1.pdf", S.IN_REVIEW, page_count=2)

    response = client.get(f"/runs/{run.id}")

    assert response.status_code == 200
    body = response.json()
    assert (body["folder"], body["folder_date"], body["state"]) == (FOLDER, "2026-10-20",
                                                                  "processing")  # fmt: skip
    assert (body["status"], body["failure_reason"]) == ("RUNNING", None)
    assert (body["files_found"], body["counts"]) == (2, {"IN_REVIEW": 1, "QUEUED": 1})
    first, second = body["files"]
    assert (first["file"], first["status"], first["pages"]) == ("inv 1.pdf", "IN_REVIEW", 2)
    assert (second["file"], second["status"]) == ("inv 2.pdf", "QUEUED")
    assert second["document_id"] is None


def test_unknown_run_is_404(client: TestClient) -> None:
    response = client.get("/runs/00000000-0000-0000-0000-000000000000")

    assert response.status_code == 404


def test_other_clients_run_is_404(client: TestClient, session: Session) -> None:
    other = Run(client_id="other-client", direction=make_run(session).direction,
                folder_date=make_run(session).folder_date)  # fmt: skip
    session.add(other)
    session.flush()

    assert client.get(f"/runs/{other.id}").status_code == 404


def test_extracted_documents_are_still_in_progress(session: Session) -> None:
    run = scanned(session, "a.pdf")
    doc(session, run, "a.pdf", S.EXTRACTED)  # verify has not run yet

    assert run_progress(session, run).state is RunState.PROCESSING


def test_run_status_is_derived_from_its_files(session: Session) -> None:
    run = scanned(session, "a.pdf")
    doc(session, run, "a.pdf", S.READ)
    assert run_progress(session, run).status is RunStatus.RUNNING

    doc(session, scanned(session, "b.pdf"), "b.pdf", S.IN_REVIEW)  # another run, settled
    finished = scanned(session, "c.pdf")
    doc(session, finished, "c.pdf", S.IN_REVIEW)
    assert run_progress(session, finished).status is RunStatus.COMPLETED

    finished.status = RunStatus.FAILED  # e.g. the shared folder was unreachable
    assert run_progress(session, finished).status is RunStatus.FAILED


def test_files_carry_their_step_timings(session: Session) -> None:
    run = scanned(session, "a.pdf", "b.pdf")
    document = make_document(session, run_id=run.id, source_path=f"{FOLDER}/a.pdf",
                             status=S.IN_REVIEW)  # fmt: skip
    session.refresh(document)
    steps = [  # (from, to, started after capture in s, details)
        (S.CAPTURED, S.READ, 10, {"work_ms": 6_000}),
        (S.READ, S.EXTRACTED, 30, {"work_ms": 200_000}),
        (S.EXTRACTED, S.VERIFIED, 240, {"work_ms": 16_000, "coding_ms": 15_000}),
    ]
    for source, target, seconds, details in steps:
        session.add(
            AuditLog(
                client_id=document.client_id, document_id=document.id, from_status=source,
                to_status=target, actor="system:test", details=details,
                created_at=document.created_at + timedelta(seconds=seconds),
            )
        )  # fmt: skip
    session.flush()

    timed, queued = run_progress(session, run).files

    assert timed.timings == StepTimings(34_000, 6_000, 200_000, 15_000, 1_000, 256_000)
    assert queued.timings is None
