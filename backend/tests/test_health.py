from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from app.core.config import Settings
from app.core.db import get_session
from app.core.redis import ping_redis
from app.main import create_app


class _UpSession:
    def execute(self, *_: object) -> None:
        return None


class _DownSession:
    def execute(self, *_: object) -> None:
        raise OperationalError("SELECT 1", None, Exception("connection refused"))


@pytest.fixture
def client() -> Iterator[TestClient]:
    app = create_app(Settings(_env_file=None, app_env="test"))
    with TestClient(app) as test_client:
        yield test_client


def test_liveness(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.parametrize(
    ("db_up", "redis_up", "code", "body"),
    [
        (True, True, 200, {"status": "ok", "database": "ok", "redis": "ok"}),
        (False, True, 503, {"status": "unavailable", "database": "unavailable", "redis": "ok"}),
        (True, False, 503, {"status": "unavailable", "database": "ok", "redis": "unavailable"}),
        (False, False, 503, {"status": "unavailable", "database": "unavailable",
                             "redis": "unavailable"}),
    ],
)  # fmt: skip
def test_readiness_reports_each_dependency(
    client: TestClient, db_up: bool, redis_up: bool, code: int, body: dict[str, str]
) -> None:
    overrides = client.app.dependency_overrides
    overrides[get_session] = lambda: _UpSession() if db_up else _DownSession()
    overrides[ping_redis] = lambda: redis_up

    response = client.get("/health/ready")

    assert response.status_code == code
    assert response.json() == body


@pytest.mark.integration
def test_readiness_ok_against_real_services() -> None:
    with TestClient(create_app()) as client:
        response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok", "redis": "ok"}
