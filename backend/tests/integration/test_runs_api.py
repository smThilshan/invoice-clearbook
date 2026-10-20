from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.db import get_session
from app.domain.enums import Direction
from app.main import create_app
from app.models import Run, RunStatus

pytestmark = pytest.mark.integration


@pytest.fixture
def enqueued() -> list[str]:
    return []


@pytest.fixture
def client(session: Session, tmp_path: Path, enqueued: list[str]) -> Iterator[TestClient]:
    from app.api.runs import get_enqueue_scan

    (tmp_path / "inbox/Purchase/2026-10-20").mkdir(parents=True)
    settings = Settings(_env_file=None, app_env="test", inbox_dir=tmp_path / "inbox")
    app = create_app(settings)
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_enqueue_scan] = lambda: enqueued.append
    with TestClient(app) as test_client:
        yield test_client


def test_create_run_stores_run_and_enqueues_scan(
    client: TestClient, session: Session, enqueued: list[str]
) -> None:
    response = client.post("/runs", json={"folder": "Purchase", "date": "2026-10-20"})

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == RunStatus.RUNNING
    run = session.get_one(Run, body["run_id"])
    assert (run.direction, run.folder_date.isoformat()) == (Direction.PURCHASE, "2026-10-20")
    assert enqueued == [body["run_id"]]


def test_missing_inbox_folder_is_404_and_nothing_enqueued(
    client: TestClient, enqueued: list[str]
) -> None:
    response = client.post("/runs", json={"folder": "Sales", "date": "2026-10-20"})

    assert response.status_code == 404
    assert "no inbox folder" in response.json()["detail"]
    assert enqueued == []


@pytest.mark.parametrize(
    "body",
    [
        {"folder": "../etc", "date": "2026-10-20"},
        {"folder": "purchase", "date": "2026-10-20"},
        {"folder": "Purchase", "date": "20-10-2026"},
        {"folder": "Purchase"},
    ],
)
def test_invalid_requests_rejected(client: TestClient, body: dict[str, str]) -> None:
    assert client.post("/runs", json=body).status_code == 422
