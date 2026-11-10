"""Decision 91: an admin removes a company (hidden, nothing deleted) and restores it."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.db import get_session
from app.domain.enums import DocumentStatus, Role
from app.main import create_app
from app.models import Client, Document, Outbox, OutboxStatus, Run, RunStatus
from tests.integration.factories import CLIENT, make_client, make_document, make_run
from tests.login import log_in

pytestmark = pytest.mark.integration

OTHER = "other-company"


@pytest.fixture
def api(session: Session, tmp_path: Path) -> Iterator[TestClient]:
    make_client(session, tmp_path / "Cafe Al Noor")
    make_client(session, tmp_path / "Other", client_id=OTHER)
    app = create_app(Settings(_env_file=None, app_env="test"))
    log_in(app)
    app.dependency_overrides[get_session] = lambda: session
    with TestClient(app) as test_client:
        yield test_client


def listed(api: TestClient, path: str = "/clients") -> list[str]:
    return [c["client_id"] for c in api.get(path).json()]


def test_a_removed_company_is_hidden_everywhere_and_kept(api: TestClient, session: Session) -> None:
    run = make_run(session)
    run.status = RunStatus.COMPLETED
    document = make_document(session, run_id=run.id, status="IN_REVIEW")

    response = api.post(f"/clients/{CLIENT}/remove")

    assert response.status_code == 200, response.text
    assert listed(api) == [OTHER]
    assert listed(api, "/clients/removed") == [CLIENT]
    assert api.get(f"/clients/{CLIENT}/runs").status_code == 404
    assert api.get(f"/documents?client_id={CLIENT}").status_code == 404
    assert api.get(f"/documents/{document.id}").status_code == 404
    # Nothing is deleted.
    assert session.get(Document, document.id) is not None
    assert session.query(Client).filter_by(client_id=CLIENT).one().is_active is False


def test_restore_brings_it_back_as_it_was(api: TestClient, session: Session) -> None:
    client = session.query(Client).filter_by(client_id=CLIENT).one()
    client.paid_through = "1000"
    api.post(f"/clients/{CLIENT}/remove")

    response = api.post(f"/clients/{CLIENT}/restore")

    assert response.status_code == 200, response.text
    assert response.json()["paid_through"] == "1000"
    assert sorted(listed(api)) == sorted([CLIENT, OTHER])
    assert listed(api, "/clients/removed") == []


def test_only_a_removed_company_can_be_restored(api: TestClient) -> None:
    assert api.post(f"/clients/{CLIENT}/restore").status_code == 404
    assert api.post("/clients/no-such-company/restore").status_code == 404
    assert api.post("/clients/no-such-company/remove").status_code == 404


def test_a_company_reading_files_is_not_removed(api: TestClient, session: Session) -> None:
    make_run(session)  # RUNNING

    response = api.post(f"/clients/{CLIENT}/remove")

    assert response.status_code == 409
    assert "still reading" in response.json()["detail"]
    assert "press Stop" in response.json()["detail"]
    assert CLIENT in listed(api)


def finished_run_with(session: Session, *statuses: DocumentStatus) -> Run:
    """A run still stored RUNNING, as every finished run is: its files say whether it reads."""
    run = make_run(session)
    run.files = [f"inv-{n}.pdf" for n in range(len(statuses))]
    for n, status in enumerate(statuses):
        make_document(session, run_id=run.id, source_path=f"inv-{n}.pdf", status=status)
    session.flush()
    return run


def test_a_finished_run_does_not_block_even_with_failed_files(
    api: TestClient, session: Session
) -> None:
    finished_run_with(session, DocumentStatus.IN_REVIEW, DocumentStatus.FAILED,
                      DocumentStatus.DUPLICATE, DocumentStatus.REJECTED)  # fmt: skip

    assert api.post(f"/clients/{CLIENT}/remove").status_code == 200


def test_a_run_with_a_file_still_waiting_blocks_until_stopped(
    api: TestClient, session: Session
) -> None:
    run = finished_run_with(session, DocumentStatus.IN_REVIEW, DocumentStatus.CAPTURED)

    assert api.post(f"/clients/{CLIENT}/remove").status_code == 409

    run.status = RunStatus.STOPPED  # what Stop on the Run screen does
    assert api.post(f"/clients/{CLIENT}/remove").status_code == 200


def test_a_stopped_or_finished_run_does_not_block(api: TestClient, session: Session) -> None:
    make_run(session).status = RunStatus.STOPPED
    make_run(session).status = RunStatus.COMPLETED

    assert api.post(f"/clients/{CLIENT}/remove").status_code == 200


@pytest.mark.parametrize("status", [OutboxStatus.PENDING, OutboxStatus.IN_PROGRESS])
def test_a_company_with_documents_waiting_to_send_is_not_removed(
    api: TestClient, session: Session, status: OutboxStatus
) -> None:
    run = make_run(session)
    run.status = RunStatus.COMPLETED
    document = make_document(session, run_id=run.id, status="APPROVED")
    session.add(Outbox(client_id=CLIENT, document_id=document.id, status=status))
    session.flush()

    response = api.post(f"/clients/{CLIENT}/remove")

    assert response.status_code == 409
    assert "1 approved document is still waiting to be sent" in response.json()["detail"]


@pytest.mark.parametrize("status", [OutboxStatus.DONE, OutboxStatus.FAILED, OutboxStatus.DEAD])
def test_finished_sends_do_not_block(
    api: TestClient, session: Session, status: OutboxStatus
) -> None:
    run = make_run(session)
    run.status = RunStatus.COMPLETED
    document = make_document(session, run_id=run.id, status="APPROVED")
    session.add(Outbox(client_id=CLIENT, document_id=document.id, status=status))
    session.flush()

    assert api.post(f"/clients/{CLIENT}/remove").status_code == 200


def test_another_companys_work_does_not_block(api: TestClient, session: Session) -> None:
    make_run(session)  # CLIENT is reading

    assert api.post(f"/clients/{OTHER}/remove").status_code == 200


def test_only_an_admin_removes_restores_or_sees_removed(api: TestClient, session: Session) -> None:
    log_in(api.app, Role.ACCOUNTANT)  # type: ignore[arg-type]

    assert api.post(f"/clients/{CLIENT}/remove").status_code == 403
    assert api.post(f"/clients/{CLIENT}/restore").status_code == 403
    assert api.get("/clients/removed").status_code == 403
    assert session.query(Client).filter_by(client_id=CLIENT).one().is_active is True
