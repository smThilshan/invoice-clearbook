"""Decision 77: live, one address serves the built Workbench and the API under /api."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app

INDEX = "<!doctype html><title>Workbench</title>"


@pytest.fixture
def site(tmp_path: Path) -> Iterator[TestClient]:
    (tmp_path / "index.html").write_text(INDEX)
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "app-1a2b.js").write_text("console.log(1)")
    app = create_app(Settings(_env_file=None, app_env="test", web_dist_dir=tmp_path))
    with TestClient(app) as client:
        yield client


def test_the_workbench_is_served_at_the_root(site: TestClient) -> None:
    response = site.get("/")
    assert response.status_code == 200
    assert response.text == INDEX


def test_a_workbench_page_opened_from_a_link_gets_the_workbench(site: TestClient) -> None:
    response = site.get("/documents/0d8c4a52-9a54-4c1e-8f1e-2f1a7d0e6b10")
    assert response.status_code == 200
    assert response.text == INDEX


def test_built_files_are_served_and_a_missing_one_is_not_found(site: TestClient) -> None:
    assert site.get("/assets/app-1a2b.js").text == "console.log(1)"
    assert site.get("/assets/app-gone.js").status_code == 404


def test_the_api_answers_under_api(site: TestClient) -> None:
    assert site.get("/api/health").json() == {"status": "ok"}
    assert site.get("/api/clients").status_code == 401  # still behind the login
    assert site.get("/api/no-such-route").status_code == 404


def test_development_serves_the_api_alone_at_the_root() -> None:
    with TestClient(create_app(Settings(_env_file=None, app_env="test"))) as client:
        assert client.get("/health").json() == {"status": "ok"}
        assert client.get("/").status_code == 404
