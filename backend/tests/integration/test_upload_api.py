"""Decision 78: a folder chosen on the accountant's own computer is uploaded and run."""

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.db import get_session
from app.main import create_app
from app.models import Run
from tests.integration.factories import CLIENT, make_client
from tests.login import log_in

pytestmark = pytest.mark.integration

PDF = b"%PDF-1.4 made-up invoice"
JPEG = b"\xff\xd8\xff made-up photo"


@pytest.fixture
def enqueued() -> list[str]:
    return []


@pytest.fixture
def uploads(tmp_path: Path) -> Path:
    return tmp_path / "uploads"


@pytest.fixture
def settings(uploads: Path) -> Settings:
    return Settings(_env_file=None, app_env="test", upload_dir=uploads)


@pytest.fixture
def client(
    session: Session, tmp_path: Path, settings: Settings, enqueued: list[str]
) -> Iterator[TestClient]:
    from app.api.runs import get_enqueue_scan

    make_client(session, tmp_path / "Cafe Al Noor")
    app = create_app(settings)
    log_in(app)
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_enqueue_scan] = lambda: enqueued.append
    with TestClient(app) as test_client:
        yield test_client


def upload(client: TestClient, files: dict[str, bytes], **form: str) -> tuple[int, dict]:  # type: ignore[type-arg]
    response = client.post(
        "/runs/upload",
        data={"client_id": CLIENT, "paths": json.dumps(list(files)), **form},
        files=[("files", (Path(name).name, content)) for name, content in files.items()],
    )
    return response.status_code, response.json()


def saved(uploads: Path) -> dict[str, bytes]:
    """Every file saved for the client, by its path inside its upload's own folder."""
    root = uploads / CLIENT
    files = sorted(path for path in root.rglob("*") if path.is_file()) if root.exists() else []
    return {"/".join(path.relative_to(root).parts[1:]): path.read_bytes() for path in files}


def test_a_chosen_folder_is_saved_with_its_sub_folders_and_run(
    client: TestClient, session: Session, uploads: Path, enqueued: list[str]
) -> None:
    status, body = upload(
        client,
        {
            "October 2026/21/bill.pdf": PDF,
            "October 2026/photo.jpg": JPEG,
            "October 2026/totals.xlsx": b"not an invoice",
            "October 2026/.DS_Store": b"finder",
        },
        folder_date="2026-10-31",
    )

    assert status == 202, body
    assert (body["folder"], body["folder_date"], body["files"]) == ("October 2026", "2026-10-31", 2)
    assert body["skipped"] == ["October 2026/totals.xlsx", "October 2026/.DS_Store"]
    assert saved(uploads) == {"October 2026/21/bill.pdf": PDF, "October 2026/photo.jpg": JPEG}
    run = session.get_one(Run, body["run_id"])
    assert Path(run.root_path).parent == uploads / CLIENT  # the upload's own folder is the place
    assert (run.folder, run.created_by) == ("October 2026", "tester@missan.test")
    assert enqueued == [body["run_id"]]


def test_files_chosen_one_by_one_are_run_from_the_upload_itself(client: TestClient) -> None:
    status, body = upload(client, {"a.pdf": PDF, "b.pdf": PDF})

    assert status == 202, body
    assert (body["folder"], body["folder_date"], body["files"]) == ("", None, 2)


def test_without_a_date_the_run_has_none(client: TestClient) -> None:
    assert upload(client, {"October 2026/bill.pdf": PDF})[1]["folder_date"] is None


def test_paths_that_could_leave_the_upload_folder_are_skipped(
    client: TestClient, uploads: Path, tmp_path: Path
) -> None:
    status, body = upload(
        client,
        {"../outside.pdf": PDF, "/etc/outside.pdf": PDF, "a/../../outside.pdf": PDF, "ok.pdf": PDF},
    )

    assert status == 202, body
    assert body["files"] == 1
    assert len(body["skipped"]) == 3
    assert saved(uploads) == {"ok.pdf": PDF}
    assert not list(tmp_path.glob("outside.pdf"))


@pytest.mark.parametrize(
    ("files", "limits", "message"),
    [
        ({"a.pdf": PDF, "b.pdf": PDF, "c.pdf": PDF}, {"max_upload_files": 2}, "at most 2 files"),
        ({"a.pdf": b"%PDF" + b"x" * 1_100_000}, {"max_upload_mb": 1}, "at most 1 MB"),
        ({"notes.txt": b"hello", "../x.pdf": PDF}, {}, "no invoice files"),
    ],
)
def test_an_upload_that_cannot_be_run_is_refused_and_nothing_stays(
    client: TestClient,
    settings: Settings,
    uploads: Path,
    files: dict[str, bytes],
    limits: dict[str, int],
    message: str,
) -> None:
    client.app.dependency_overrides[get_settings] = lambda: settings.model_copy(update=limits)  # type: ignore[attr-defined]

    status, body = upload(client, files)

    assert status == 422
    assert message in body["detail"]
    assert not (uploads / CLIENT).exists() or not any((uploads / CLIENT).iterdir())


def test_one_path_is_needed_per_file(client: TestClient) -> None:
    response = client.post(
        "/runs/upload",
        data={"client_id": CLIENT, "paths": json.dumps(["a.pdf"])},
        files=[("files", ("a.pdf", PDF)), ("files", ("b.pdf", PDF))],
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "one path per file"


def test_an_unknown_client_is_not_found(client: TestClient) -> None:
    response = client.post(
        "/runs/upload",
        data={"client_id": "nobody", "paths": json.dumps(["a.pdf"])},
        files=[("files", ("a.pdf", PDF))],
    )

    assert response.status_code == 404


def test_the_date_a_folder_name_stands_for(client: TestClient) -> None:
    assert client.get("/folders/date", params={"name": "October 2026"}).json() == {
        "date": "2026-10-31"
    }
    assert client.get("/folders/date", params={"name": "Supplier bills"}).json() == {"date": None}


def test_the_worker_finds_the_uploaded_files_as_in_any_folder(
    client: TestClient, session: Session
) -> None:
    from app.pipeline.ingest import scan_run
    from app.workers.tasks import _client_root

    _, body = upload(client, {"October 2026/21/bill.pdf": PDF, "October 2026/photo.jpg": JPEG})
    run = session.get_one(Run, body["run_id"])

    assert sorted(scan_run(session, run, _client_root(session, run))) == [
        "October 2026/21/bill.pdf",
        "October 2026/photo.jpg",
    ]
