from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from app.adapters.llm import ModelStatus
from app.api.health import get_model_status
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


def ready(
    client: TestClient, *, db_up: bool = True, redis_up: bool = True, model: str = "ok"
) -> tuple[int, dict[str, str]]:
    overrides = client.app.dependency_overrides
    overrides[get_session] = lambda: _UpSession() if db_up else _DownSession()
    overrides[ping_redis] = lambda: redis_up
    overrides[get_model_status] = lambda: ModelStatus(model)
    response = client.get("/health/ready")
    return response.status_code, response.json()


def test_liveness(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.parametrize(
    ("db_up", "redis_up", "code", "overall", "database", "redis"),
    [
        (True, True, 200, "ok", "ok", "ok"),
        (False, True, 503, "unavailable", "unavailable", "ok"),
        (True, False, 503, "unavailable", "ok", "unavailable"),
        (False, False, 503, "unavailable", "unavailable", "unavailable"),
    ],
)
def test_readiness_reports_each_dependency(
    client: TestClient,
    db_up: bool,
    redis_up: bool,
    code: int,
    overall: str,
    database: str,
    redis: str,
) -> None:
    status_code, body = ready(client, db_up=db_up, redis_up=redis_up)

    assert status_code == code
    assert body == {"status": overall, "database": database, "redis": redis, "model": "ok"}


@pytest.mark.parametrize("model", ["unreachable", "missing", "incompatible"])
def test_model_problems_are_reported_but_do_not_make_api_unready(
    client: TestClient, model: str
) -> None:
    status_code, body = ready(client, model=model)

    assert status_code == 200
    assert (body["status"], body["model"]) == ("ok", model)


@pytest.mark.integration
def test_readiness_ok_against_real_services() -> None:
    with TestClient(create_app()) as client:
        response = client.get("/health/ready")

    assert response.status_code == 200
    body = response.json()
    assert (body["status"], body["database"], body["redis"]) == ("ok", "ok", "ok")
    assert body["model"] in {status.value for status in ModelStatus}  # Ollama may be off
