"""Logging in through the real API (decision 71): cookie, lockout, deactivation, roles and the
X-Clearbook header. Made-up users only."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.auth import COOKIE, get_login_redis
from app.core.config import Settings, get_settings
from app.core.db import get_session
from app.domain.enums import DocumentStatus, Role
from app.main import create_app
from app.models import AuditLog
from app.services import users
from tests.integration.factories import CLIENT, make_client, make_document

pytestmark = pytest.mark.integration

SETTINGS = Settings(_env_file=None, app_env="test", jwt_secret="made-up-secret-" + "x" * 40)
PASSWORD = "made-up password 1"
CHANGE = {"X-Clearbook": "1"}


class FakeRedis:
    """The few Redis calls the lockout makes."""

    def __init__(self) -> None:
        self.values: dict[str, int] = {}

    def get(self, key: str) -> bytes | None:
        return str(self.values[key]).encode() if key in self.values else None

    def incr(self, key: str) -> int:
        self.values[key] = self.values.get(key, 0) + 1
        return self.values[key]

    def expire(self, key: str, seconds: int) -> None:
        pass

    def delete(self, key: str) -> None:
        self.values.pop(key, None)


@pytest.fixture
def api(session: Session, tmp_path: Path) -> Iterator[TestClient]:
    make_client(session, tmp_path / "Cafe Al Noor")
    users.create_user(session, email="Admin@Missan.test", name="Made-up Admin", role=Role.ADMIN,
                      password=PASSWORD)  # fmt: skip
    users.create_user(session, email="accountant@missan.test", name="Made-up Accountant",
                      role=Role.ACCOUNTANT, password=PASSWORD)  # fmt: skip
    app = create_app(SETTINGS)
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_settings] = lambda: SETTINGS
    redis = FakeRedis()
    app.dependency_overrides[get_login_redis] = lambda: redis
    with TestClient(app) as client:
        yield client


def log_in(api: TestClient, email: str = "admin@missan.test", password: str = PASSWORD) -> object:
    return api.post("/auth/login", json={"email": email, "password": password}, headers=CHANGE)


def test_logging_in_sets_an_httponly_strict_cookie_and_names_the_user(api: TestClient) -> None:
    before = api.get("/clients")
    answer = log_in(api, email="  ADMIN@missan.test ")  # any case, spaces trimmed

    assert before.status_code == 401
    assert answer.status_code == 200, answer.text  # type: ignore[attr-defined]
    cookie = answer.headers["set-cookie"].lower()  # type: ignore[attr-defined]
    assert f"{COOKIE}=" in cookie and "httponly" in cookie and "samesite=strict" in cookie
    assert api.get("/auth/me").json() == {"email": "admin@missan.test", "name": "Made-up Admin",
                                          "role": "ADMIN"}  # fmt: skip
    assert api.get("/clients").status_code == 200


def test_a_wrong_password_and_an_unknown_email_get_the_same_answer(api: TestClient) -> None:
    wrong = log_in(api, password="not the password")
    unknown = log_in(api, email="nobody@missan.test")

    assert wrong.status_code == unknown.status_code == 401  # type: ignore[attr-defined]
    assert wrong.json() == unknown.json() == {"detail": "Wrong email or password."}  # type: ignore[attr-defined]


def test_too_many_wrong_attempts_lock_the_email_even_for_the_right_password(
    api: TestClient,
) -> None:
    for _ in range(SETTINGS.login_max_failures):
        log_in(api, password="not the password")

    locked = log_in(api)

    assert locked.status_code == 429  # type: ignore[attr-defined]
    assert "15 minutes" in locked.json()["detail"]  # type: ignore[attr-defined]


def test_deactivating_or_a_new_password_ends_every_session_at_once(
    api: TestClient, session: Session
) -> None:
    log_in(api)
    users.set_password(session, "admin@missan.test", "another made-up password")
    after_password = api.get("/auth/me")
    log_in(api, password="another made-up password")
    users.deactivate(session, "admin@missan.test")

    assert after_password.status_code == 401
    assert api.get("/auth/me").status_code == 401
    assert log_in(api, password="another made-up password").status_code == 401  # type: ignore[attr-defined]


def test_a_change_without_the_app_header_is_refused_even_when_logged_in(api: TestClient) -> None:
    log_in(api)

    refused = api.post("/clients", json={"name": "Made-up Bistro"})
    allowed = api.post("/clients", json={"name": "Made-up Bistro"}, headers=CHANGE)

    assert refused.status_code == 403 and "X-Clearbook" in refused.json()["detail"]
    assert allowed.status_code == 201


def test_only_an_admin_connects_or_syncs_the_accounting_system(api: TestClient) -> None:
    log_in(api, email="accountant@missan.test")
    sync = api.post(f"/clients/{CLIENT}/accounting/sync", headers=CHANGE)
    connect = api.get("/xero/connect", params={"client_id": CLIENT}, follow_redirects=False)
    status = api.get(f"/clients/{CLIENT}/accounting")

    assert sync.status_code == connect.status_code == 403
    assert status.status_code == 200  # seeing the connection is every accountant's
    log_in(api)
    assert api.post(f"/clients/{CLIENT}/accounting/sync", headers=CHANGE).status_code == 409


def test_logging_out_ends_the_session(api: TestClient) -> None:
    log_in(api)

    out = api.post("/auth/logout", headers=CHANGE)

    assert out.status_code == 204
    assert api.get("/auth/me").status_code == 401


def test_without_a_real_secret_nobody_can_log_in(api: TestClient) -> None:
    unset = Settings(_env_file=None, app_env="test", jwt_secret="change-me")
    api.app.dependency_overrides[get_settings] = lambda: unset  # type: ignore[attr-defined]

    answer = log_in(api)

    assert answer.status_code == 503  # type: ignore[attr-defined]
    assert "make jwt-secret" in answer.json()["detail"]  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    ("email", "password", "problem"),
    [
        ("not-an-email", PASSWORD, "not an email address"),
        ("new@missan.test", "short", "at least 12 characters"),
        ("ADMIN@missan.test", PASSWORD, "already exists"),  # same email, any case
    ],
)
def test_a_user_needs_an_email_a_long_password_and_no_twin(
    api: TestClient, session: Session, email: str, password: str, problem: str
) -> None:
    with pytest.raises(users.UserError, match=problem):
        users.create_user(session, email=email, name="Made-up", role=Role.ACCOUNTANT,
                          password=password)  # fmt: skip


def test_what_a_user_does_is_recorded_under_their_own_email(
    api: TestClient, session: Session
) -> None:
    doc = make_document(session, status=DocumentStatus.IN_REVIEW)
    log_in(api, email="accountant@missan.test")

    answer = api.post(f"/documents/{doc.id}/reject", json={"reason": "DUPLICATE"}, headers=CHANGE)

    assert answer.status_code == 200, answer.text
    last = session.scalars(select(AuditLog).where(AuditLog.document_id == doc.id)
                           .order_by(AuditLog.id.desc())).first()  # fmt: skip
    assert last is not None and (last.to_status, last.actor) == (
        DocumentStatus.REJECTED, "accountant@missan.test")  # fmt: skip
