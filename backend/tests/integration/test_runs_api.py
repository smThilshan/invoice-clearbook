from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.db import get_session
from app.domain.enums import Direction
from app.main import create_app
from app.models import Run, RunStatus
from tests.integration.factories import CLIENT, make_client

pytestmark = pytest.mark.integration


@pytest.fixture
def enqueued() -> list[str]:
    return []


@pytest.fixture
def client(session: Session, tmp_path: Path, enqueued: list[str]) -> Iterator[TestClient]:
    from app.api.runs import get_enqueue_scan

    root = tmp_path / "Cafe Al Noor"
    (root / "October 2026" / "21").mkdir(parents=True)
    (root / "Supplier bills").mkdir()
    make_client(session, root)
    make_client(session, tmp_path / "Other Restaurant", client_id="other-restaurant")
    app = create_app(Settings(_env_file=None, app_env="test"))
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_enqueue_scan] = lambda: enqueued.append
    with TestClient(app) as test_client:
        yield test_client


def start(client: TestClient, **body: object) -> dict[str, object]:
    response = client.post("/runs", json={"client_id": CLIENT, **body})
    assert response.status_code == 202, response.text
    result: dict[str, object] = response.json()
    return result


def test_run_on_a_day_folder_takes_its_date(
    client: TestClient, session: Session, enqueued: list[str]
) -> None:
    body = start(client, folder="October 2026/21")

    assert (body["folder"], body["folder_date"], body["status"]) == (
        "October 2026/21", "2026-10-21", RunStatus.RUNNING
    )  # fmt: skip
    run = session.get_one(Run, body["run_id"])
    assert (run.client_id, run.direction) == (CLIENT, Direction.PURCHASE)
    assert enqueued == [body["run_id"]]


def test_run_on_a_month_folder_uses_the_month_end(client: TestClient) -> None:
    assert start(client, folder="October 2026")["folder_date"] == "2026-10-31"


def test_folder_without_a_date_has_none(client: TestClient) -> None:
    assert start(client, folder="Supplier bills")["folder_date"] is None


def test_date_confirmed_by_the_user_wins(client: TestClient) -> None:
    body = start(client, folder="Supplier bills", folder_date="2026-10-15")

    assert body["folder_date"] == str(date(2026, 10, 15))


def test_user_can_confirm_there_is_no_folder_date(client: TestClient) -> None:
    assert start(client, folder="October 2026/21", folder_date=None)["folder_date"] is None


def test_folder_names_without_a_year_get_the_most_recent_date(
    client: TestClient, tmp_path: Path
) -> None:
    from app.core.config import get_settings

    class FixedDay(Settings):
        def business_today(self) -> date:
            return date(2026, 10, 21)

    (tmp_path / "Cafe Al Noor" / "May" / "May 1").mkdir(parents=True)
    client.app.dependency_overrides[get_settings] = lambda: FixedDay(_env_file=None)  # type: ignore[attr-defined]

    assert start(client, folder="May/May 1")["folder_date"] == "2026-05-01"
    listing = client.get(f"/clients/{CLIENT}/folders", params={"path": "May"}).json()
    assert listing["folders"] == [{"name": "May 1", "path": "May/May 1", "date": "2026-05-01"}]


def test_whole_client_folder_can_be_run(client: TestClient) -> None:
    assert start(client)["folder"] == ""


@pytest.mark.parametrize("folder", ["../Other Restaurant", "/etc", "October 2026/../../x"])
def test_folders_outside_the_restaurant_are_refused(
    client: TestClient, enqueued: list[str], folder: str
) -> None:
    response = client.post("/runs", json={"client_id": CLIENT, "folder": folder})

    assert response.status_code == 404
    assert enqueued == []


def test_missing_folder_or_client_is_404(client: TestClient, enqueued: list[str]) -> None:
    missing_folder = client.post("/runs", json={"client_id": CLIENT, "folder": "December 2030"})
    missing_client = client.post("/runs", json={"client_id": "nobody", "folder": ""})

    assert (missing_folder.status_code, missing_client.status_code) == (404, 404)
    assert "folder not found" in missing_folder.json()["detail"]
    assert enqueued == []


# --- folder picker --------------------------------------------------------------------------


def test_clients_are_listed_for_the_picker(client: TestClient) -> None:
    listed = [(c["client_id"], c["name"]) for c in client.get("/clients").json()]

    assert listed == [("other-restaurant", "Test other-restaurant"), (CLIENT, f"Test {CLIENT}")]


def test_folders_are_browsed_level_by_level_with_their_dates(client: TestClient) -> None:
    top = client.get(f"/clients/{CLIENT}/folders").json()
    month = client.get(f"/clients/{CLIENT}/folders", params={"path": "October 2026"}).json()

    assert [(f["name"], f["date"]) for f in top["folders"]] == [
        ("October 2026", "2026-10-31"), ("Supplier bills", None)
    ]  # fmt: skip
    assert month["folders"] == [{"name": "21", "path": "October 2026/21", "date": "2026-10-21"}]


def test_browsing_outside_the_restaurant_is_refused(client: TestClient) -> None:
    response = client.get(f"/clients/{CLIENT}/folders", params={"path": "../Other Restaurant"})

    assert response.status_code == 404
