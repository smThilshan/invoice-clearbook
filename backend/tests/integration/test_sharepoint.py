"""SharePoint, read only (decision 83), through the API against a made-up Microsoft: an admin
connects once, every user browses and runs, a folder is fetched with its sub-folders and then run
like any folder, and nothing but reading is ever asked of SharePoint."""

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.domain.enums import Role
from app.models import Run, RunStatus, SharePointConnection
from app.pipeline import ingest
from app.services import sharepoint
from app.services.sharepoint import SharePointError
from app.services.sharepoint_runs import fetch
from tests.integration.factories import CLIENT, make_client
from tests.login import log_in
from tests.sharepoint_fake import DRIVE, FakeMicrosoft
from tests.sharepoint_fake import settings as fake_settings

pytestmark = pytest.mark.integration


@pytest.fixture
def fake() -> FakeMicrosoft:
    return FakeMicrosoft()


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return fake_settings(upload_dir=tmp_path / "uploads")


@pytest.fixture
def enqueued() -> list[str]:
    return []


@pytest.fixture
def role() -> Role:
    return Role.ADMIN


@pytest.fixture
def api(
    session: Session,
    tmp_path: Path,
    settings: Settings,
    fake: FakeMicrosoft,
    enqueued: list[str],
    role: Role,
) -> Iterator[TestClient]:
    from app.api.runs import get_enqueue_scan
    from app.api.sharepoint import get_microsoft
    from app.core.config import get_settings
    from app.core.db import get_session
    from app.main import create_app

    make_client(session, tmp_path / "Cafe Al Noor")
    app = create_app(settings)
    log_in(app, role)
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_microsoft] = fake.microsoft
    app.dependency_overrides[get_enqueue_scan] = lambda: enqueued.append
    with TestClient(app, follow_redirects=False) as client:
        yield client


def connect(api: TestClient) -> dict[str, list[str]]:
    """Sign in as the admin does; returns what the Workbench page is told."""
    start = api.get("/sharepoint/connect", params={"back": "run"})
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    back = api.get("/sharepoint/callback", params={"code": "code-1", "state": state})
    assert back.status_code == 303
    assert back.headers["location"].startswith("http://127.0.0.1:5173/run?")
    return parse_qs(urlparse(back.headers["location"]).query)


def test_an_admin_connects_once_and_the_access_is_kept_encrypted(
    api: TestClient, session: Session
) -> None:
    told = connect(api)

    assert told == {"sharepoint": ["Made-up Admin"]}
    stored = session.scalars(select(SharePointConnection)).one()
    assert stored.account_email == "admin@missan.test"
    assert stored.connected_by == "tester@missan.test"
    assert "access-1" not in stored.access_token and "refresh-1" not in stored.refresh_token
    status = api.get("/sharepoint/status").json()
    assert status["connected"] and status["account_name"] == "Made-up Admin"


def test_connecting_again_replaces_the_earlier_sign_in(api: TestClient, session: Session) -> None:
    connect(api)
    connect(api)

    assert len(session.scalars(select(SharePointConnection)).all()) == 1


def test_a_sign_in_not_started_here_is_refused(api: TestClient, session: Session) -> None:
    back = api.get("/sharepoint/callback", params={"code": "c", "state": "sharepoint|run|1|x|a.b"})

    assert (
        "not started from the Workbench"
        in parse_qs(urlparse(back.headers["location"]).query)["sharepoint_error"][0]
    )
    assert session.scalars(select(SharePointConnection)).first() is None


@pytest.mark.parametrize("role", [Role.ACCOUNTANT])
def test_an_accountant_cannot_connect_or_disconnect(api: TestClient) -> None:
    assert api.get("/sharepoint/connect").status_code == 403
    assert api.post("/sharepoint/disconnect").status_code == 403


def test_before_connecting_browsing_says_who_connects(api: TestClient) -> None:
    answer = api.get("/sharepoint/sites")

    assert answer.status_code == 409
    assert "an admin connects it on the Organization or Business page" in answer.json()["detail"]


def test_sites_libraries_and_folders_are_browsed_with_their_dates(api: TestClient) -> None:
    connect(api)

    sites = api.get("/sharepoint/sites").json()
    libraries = api.get("/sharepoint/sites/s-roast/libraries").json()
    april = api.get("/sharepoint/folder", params={"drive_id": DRIVE, "item_id": "f-april"}).json()
    day = api.get("/sharepoint/folder", params={"drive_id": DRIVE, "item_id": "f-a12"}).json()

    assert [s["name"] for s in sites] == ["Finance", "ROAST HUB"]
    assert libraries == [{"drive_id": DRIVE, "name": "Documents"}]
    assert april["folder"]["path"] == "ROAST/OUTLETS/April"
    assert [f["name"] for f in april["folders"]] == ["April 11", "April 12"]  # hidden left out
    eleventh = april["folders"][0]
    assert (eleventh["items"], eleventh["item_id"]) == (3, "f-a11")
    assert date.fromisoformat(eleventh["date"]).timetuple()[1:3] == (4, 11)  # no year: latest
    assert (day["invoice_files"], day["other_files"]) == (1, 1)  # the Office lock file is not


def test_a_pasted_link_opens_its_folder_and_a_file_link_is_refused(api: TestClient) -> None:
    connect(api)

    folder = api.get("/sharepoint/link", params={
        "url": "https://missan.sharepoint.com/:f:/s/ROASTHUB/April-made-up"})  # fmt: skip
    file = api.get("/sharepoint/link", params={
        "url": "https://missan.sharepoint.com/:b:/s/ROASTHUB/bill-made-up"})  # fmt: skip

    assert (folder.json()["item_id"], folder.json()["name"]) == ("f-april", "April")
    assert file.status_code == 409 and "link is to a file" in file.json()["detail"]


def test_an_expired_access_is_renewed_and_the_new_sign_in_kept(
    api: TestClient, session: Session, fake: FakeMicrosoft
) -> None:
    connect(api)
    stored = session.scalars(select(SharePointConnection)).one()
    stored.expires_at = datetime.now(UTC)

    assert api.get("/sharepoint/sites").status_code == 200
    assert fake.issued == 2
    assert stored.expires_at > datetime.now(UTC) + timedelta(minutes=30)


def test_an_access_graph_turns_away_is_renewed_once(api: TestClient, fake: FakeMicrosoft) -> None:
    connect(api)
    fake.refused_tokens.add("access-1")

    assert api.get("/sharepoint/sites").status_code == 200
    assert fake.issued == 2


def test_a_withdrawn_sign_in_asks_for_a_new_one_and_stays_marked(
    api: TestClient, session: Session, fake: FakeMicrosoft
) -> None:
    connect(api)
    session.scalars(select(SharePointConnection)).one().expires_at = datetime.now(UTC)
    fake.revoked = True

    answer = api.get("/sharepoint/sites")

    assert answer.json()["detail"] == (
        "Microsoft no longer accepts the SharePoint sign-in: an admin connects SharePoint again"
    )
    status = api.get("/sharepoint/status").json()
    assert status["needs_reconnect"] and "no longer accepts" in status["reconnect_reason"]
    assert api.get("/sharepoint/sites").json()["detail"] == answer.json()["detail"]


def test_disconnecting_forgets_the_sign_in(api: TestClient, session: Session) -> None:
    connect(api)

    assert api.post("/sharepoint/disconnect").json()["connected"] is False
    assert session.scalars(select(SharePointConnection)).first() is None


def start_run(api: TestClient, item_id: str = "f-april", **more: object) -> dict[str, object]:
    answer = api.post("/runs/sharepoint", json={
        "client_id": CLIENT, "drive_id": DRIVE, "item_id": item_id, **more})  # fmt: skip
    assert answer.status_code == 202, answer.text
    return answer.json()  # type: ignore[no-any-return]


def test_a_folder_is_fetched_with_its_sub_folders_then_scanned_like_any_folder(
    api: TestClient, session: Session, settings: Settings, fake: FakeMicrosoft,
    enqueued: list[str],
) -> None:  # fmt: skip
    connect(api)
    accepted = start_run(api)
    run = session.get(Run, accepted["run_id"])
    assert run is not None and enqueued == [str(run.id)]
    assert api.get(f"/runs/{run.id}").json()["state"] == "fetching"

    assert fetch(session, settings, run, fake.microsoft()) is True
    paths = ingest.scan_run(session, run, Path(str(run.root_path)))

    assert paths == ["April/April 11/bill.pdf", "April/April 11/photo.JPG",
                     "April/April 12/receipt.pdf"]  # fmt: skip
    root = Path(str(run.root_path))
    assert (root / "April/April 11/bill.pdf").read_bytes() == b"%PDF-1.4 made-up bill"
    assert not list(root.rglob(".*"))  # no half-fetched file left behind
    progress = api.get(f"/runs/{run.id}").json()
    assert (progress["fetched"], progress["fetch_total"]) == (3, 3)
    assert progress["sharepoint_path"] == "ROAST/OUTLETS/April"
    assert progress["folder"] == "April"
    assert progress["state"] == "processing"


def test_the_run_takes_its_date_from_the_folder_path_or_the_users(api: TestClient) -> None:
    connect(api)

    by_name = start_run(api, item_id="f-a11")
    by_user = start_run(api, item_id="f-a11", folder_date="2026-04-30")
    none = start_run(api, item_id="f-a11", folder_date=None)

    assert str(by_name["folder_date"]).endswith("-04-11")
    assert (by_user["folder_date"], none["folder_date"]) == ("2026-04-30", None)


def test_fetching_again_keeps_what_was_already_fetched(
    api: TestClient, session: Session, settings: Settings, fake: FakeMicrosoft
) -> None:
    connect(api)
    run = session.get(Run, start_run(api)["run_id"])
    assert run is not None
    fetch(session, settings, run, fake.microsoft())
    downloads = sum(1 for r in fake.requests if r.url.host == "missan.sharepoint.com")

    fetch(session, settings, run, fake.microsoft())

    assert sum(1 for r in fake.requests if r.url.host == "missan.sharepoint.com") == downloads


def test_a_stopped_run_stops_fetching(
    api: TestClient, session: Session, settings: Settings, fake: FakeMicrosoft
) -> None:
    connect(api)
    run = session.get(Run, start_run(api)["run_id"])
    assert run is not None
    run.status = RunStatus.STOPPED

    assert fetch(session, settings, run, fake.microsoft()) is False
    assert run.fetched == 0


def test_a_folder_over_the_limit_is_refused_with_the_reason(
    api: TestClient, session: Session, settings: Settings, fake: FakeMicrosoft
) -> None:
    connect(api)
    run = session.get(Run, start_run(api)["run_id"])
    assert run is not None

    with pytest.raises(SharePointError, match="at most 2 in one run"):
        fetch(session, settings.model_copy(update={"max_upload_files": 2}), run, fake.microsoft())


def test_a_file_cannot_be_run_as_a_folder(api: TestClient) -> None:
    connect(api)

    answer = api.post("/runs/sharepoint", json={
        "client_id": CLIENT, "drive_id": DRIVE, "item_id": "i-bill"})  # fmt: skip

    assert answer.status_code == 409 and "is a file" in answer.json()["detail"]


def test_sharepoint_is_only_ever_read(
    api: TestClient, session: Session, settings: Settings, fake: FakeMicrosoft
) -> None:
    """Connect, browse, link, run and fetch: every request to SharePoint is a GET. The only
    other request is the sign-in's own token exchange with Microsoft's login service."""
    connect(api)
    api.get("/sharepoint/sites")
    api.get("/sharepoint/sites/s-roast/libraries")
    api.get("/sharepoint/folder", params={"drive_id": DRIVE, "item_id": "f-april"})
    api.get("/sharepoint/link", params={"url": "https://missan.sharepoint.com/:f:/s/R/April"})
    run = session.get(Run, start_run(api)["run_id"])
    assert run is not None
    fetch(session, settings, run, fake.microsoft())

    assert {r.method for r in fake.graph_requests()} == {"GET"}
    others = {(r.method, r.url.host, r.url.path) for r in fake.requests} - {
        (r.method, r.url.host, r.url.path) for r in fake.graph_requests()
    }
    assert {(method, host) for method, host, _ in others} == {("POST", "login.microsoftonline.com")}


def test_the_status_says_when_sharepoint_is_not_set_up(
    session: Session, settings: Settings
) -> None:
    unset = settings.model_copy(update={"sharepoint_client_id": ""})

    assert sharepoint.status(session, unset).configured is False
