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


# --- decision 35: any client, any folder in the allowed places --------------------------


@pytest.fixture
def anywhere(session: Session, tmp_path: Path, enqueued: list[str]) -> Iterator[TestClient]:
    from app.api.runs import get_enqueue_scan

    (tmp_path / "mac" / "Invoice tests" / "May 5").mkdir(parents=True)
    (tmp_path / "mac" / "Invoice tests" / "May 5" / "bill.pdf").write_bytes(b"%PDF-1.4")
    make_client(session, tmp_path / "Cafe Al Noor")
    settings = Settings(_env_file=None, app_env="test", folder_places=[tmp_path / "mac"])
    app = create_app(settings)
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_enqueue_scan] = lambda: enqueued.append
    from app.core.config import get_settings

    app.dependency_overrides[get_settings] = lambda: settings
    with TestClient(app) as test_client:
        yield test_client


def test_a_client_is_created_from_a_name(anywhere: TestClient) -> None:
    first = anywhere.post("/clients", json={"name": "Blue Sky Trading LLC"})
    second = anywhere.post("/clients", json={"name": "Blue Sky Trading LLC"})

    assert first.status_code == 201
    assert first.json() == {"client_id": "blue-sky-trading-llc", "name": "Blue Sky Trading LLC"}
    assert second.json()["client_id"] == "blue-sky-trading-llc-2"
    assert anywhere.post("/clients", json={"name": ""}).status_code == 422


def test_places_and_folders_can_be_browsed(anywhere: TestClient, tmp_path: Path) -> None:
    places = anywhere.get("/folders/places", params={"client_id": CLIENT}).json()
    listing = anywhere.get("/folders", params={"place": "p0", "path": "Invoice tests"}).json()

    assert [p["key"] for p in places] == ["client", "p0"]
    assert places[1]["path"] == str(tmp_path / "mac")
    assert listing["folders"] == [
        {"name": "May 5", "path": "Invoice tests/May 5", "date": "2026-05-05"}
    ]
    assert anywhere.get("/folders", params={"place": "p0", "path": ".."}).status_code == 404


def test_a_run_reads_from_the_place_it_was_picked_in(
    anywhere: TestClient, session: Session, tmp_path: Path
) -> None:
    new = anywhere.post("/clients", json={"name": "Blue Sky"}).json()["client_id"]

    response = anywhere.post(
        "/runs", json={"client_id": new, "place": "p0", "folder": "Invoice tests/May 5"}
    )

    assert response.status_code == 202, response.text
    run = session.get_one(Run, response.json()["run_id"])
    assert (run.root_path, run.folder) == (str(tmp_path / "mac"), "Invoice tests/May 5")
    assert response.json()["folder_date"] == "2026-05-05"


def test_a_client_without_a_folder_must_pick_a_place(anywhere: TestClient) -> None:
    new = anywhere.post("/clients", json={"name": "No Folder"}).json()["client_id"]

    assert anywhere.post("/runs", json={"client_id": new, "folder": ""}).status_code == 404


def test_the_worker_reads_a_run_from_its_place(
    anywhere: TestClient, session: Session, tmp_path: Path
) -> None:
    from app.workers.tasks import _client_root

    picked = anywhere.post("/runs", json={"client_id": CLIENT, "place": "p0", "folder": ""})
    own = anywhere.post("/runs", json={"client_id": CLIENT, "folder": ""})

    assert _client_root(session, session.get_one(Run, picked.json()["run_id"])) == tmp_path / "mac"
    assert _client_root(session, session.get_one(Run, own.json()["run_id"])) == (
        tmp_path / "Cafe Al Noor"
    )
