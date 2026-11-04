"""Connecting a client to Zoho Books (decision 74) through the API, against a fake Zoho: the data
centre the sign-in names is kept with the connection and used for every later call."""

from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.adapters.accounting.registry import PROVIDERS, Registry
from app.adapters.accounting.zoho.auth import ZohoAuth
from app.core.config import Settings
from app.domain.enums import AccountingProvider
from app.services import accounting_connections as connections
from tests.integration.factories import CLIENT, make_client
from tests.integration.test_xero_connection import SETTINGS as XERO_SETTINGS
from tests.login import log_in

pytestmark = pytest.mark.integration

SETTINGS = Settings(
    _env_file=None,
    zoho_client_id="app-id",
    zoho_client_secret="app-secret",
    token_key=XERO_SETTINGS.token_key,
)


class FakeZoho:
    """Zoho's accounts and organisations services, as plain HTTP."""

    def __init__(self) -> None:
        self.hosts: list[str] = []
        self.revoked: list[str] = []
        self.organisations = [{"organization_id": "905001", "name": "Missan Test"}]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.hosts.append(request.url.host)
        if request.url.path == "/oauth/v2/token/revoke":
            self.revoked.append(request.url.params["token"])
            return httpx.Response(200, json={"status": "success"})
        if request.url.path == "/oauth/v2/token":
            form = request.content.decode()
            refreshing = "grant_type=refresh_token" in form
            return httpx.Response(200, json={
                "access_token": "access-2" if refreshing else "access-1", "expires_in": 3600,
                **({} if refreshing else {"refresh_token": "refresh-1"}),
            })  # fmt: skip
        if request.url.path == "/books/v3/organizations":
            return httpx.Response(200, json={"code": 0, "organizations": self.organisations})
        return httpx.Response(404)


def zoho_registry(zoho: FakeZoho) -> Registry:
    http = httpx.Client(transport=httpx.MockTransport(zoho))
    entry = replace(PROVIDERS[AccountingProvider.ZOHO], connector=lambda s: ZohoAuth(s, http))
    return {AccountingProvider.ZOHO: entry}


@pytest.fixture
def zoho() -> FakeZoho:
    return FakeZoho()


@pytest.fixture
def api(session: Session, tmp_path: Path, zoho: FakeZoho) -> Iterator[TestClient]:
    from app.api.accounting import get_enqueue_sweep, get_registry
    from app.core.config import get_settings
    from app.core.db import get_session
    from app.main import create_app

    make_client(session, tmp_path / "Cafe Al Noor")
    app = create_app(SETTINGS)
    log_in(app)
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_settings] = lambda: SETTINGS
    app.dependency_overrides[get_registry] = lambda: zoho_registry(zoho)
    app.dependency_overrides[get_enqueue_sweep] = lambda: lambda client_id: None
    with TestClient(app, follow_redirects=False) as client:
        yield client


def sign_in(api: TestClient, server: str = "https://accounts.zoho.ae") -> httpx.Response:
    start = api.get("/zoho/connect", params={"client_id": CLIENT})
    state = start.headers["location"].split("state=")[1]
    return api.get(
        "/zoho/callback",
        params={"code": "code-1", "state": state, "location": "ae", "accounts-server": server},
    )


def test_a_client_connects_in_its_data_centre(
    api: TestClient, session: Session, zoho: FakeZoho
) -> None:
    back = sign_in(api)
    status = api.get(f"/clients/{CLIENT}/accounting").json()
    connection = connections.connection_of(session, CLIENT)

    assert "connected=Missan+Test" in back.headers["location"]
    assert "system=Zoho+Books" in back.headers["location"]
    assert zoho.hosts == ["accounts.zoho.ae", "www.zohoapis.ae"]
    assert connection is not None
    assert (connection.tenant_id, connection.region) == ("905001", "ae")
    assert "refresh-1" not in connection.refresh_token  # stored encrypted
    assert (status["provider_name"], status["organisation"]) == ("Zoho Books", "Missan Test")


def test_renewing_and_disconnecting_use_the_connections_data_centre(
    api: TestClient, session: Session, zoho: FakeZoho
) -> None:
    sign_in(api)
    connection = connections.connection_of(session, CLIENT)
    assert connection is not None
    connection.expires_at = datetime.now(UTC) + timedelta(seconds=30)
    zoho.hosts.clear()

    token = connections.access_token(session, SETTINGS, zoho_registry(zoho), CLIENT)
    connections.disconnect(session, SETTINGS, zoho_registry(zoho), CLIENT)

    assert token == "access-2"
    assert zoho.hosts == ["accounts.zoho.ae", "accounts.zoho.ae"]
    assert zoho.revoked == ["refresh-1"]  # renewing kept the one refresh token
    assert connections.connection_of(session, CLIENT) is None


def test_a_login_that_opens_several_organisations_is_not_guessed(
    api: TestClient, session: Session, zoho: FakeZoho
) -> None:
    zoho.organisations.append({"organization_id": "905002", "name": "Another Client LLC"})

    back = sign_in(api)

    assert "opens+2+organisations" in back.headers["location"]
    assert connections.connection_of(session, CLIENT) is None


def test_a_forged_data_centre_is_refused_before_any_call(api: TestClient, zoho: FakeZoho) -> None:
    back = sign_in(api, server="https://accounts.zoho.evil.com")

    assert "data+centre+we+do+not+know" in back.headers["location"]
    assert zoho.hosts == []


def test_master_data_and_sending_say_they_are_not_available_yet(api: TestClient) -> None:
    sign_in(api)

    synced = api.post(f"/clients/{CLIENT}/accounting/sync")

    assert synced.status_code == 409, synced.text
    assert "not available for Zoho Books yet" in synced.text


def test_zoho_without_its_app_keys_says_where_they_go(session: Session, tmp_path: Path) -> None:
    client = make_client(session, tmp_path / "Cafe Al Noor")
    no_keys = Settings(_env_file=None, token_key=SETTINGS.token_key)

    with pytest.raises(connections.AccountingConnectionError, match=r"app keys go in \.env"):
        connections.start(no_keys, PROVIDERS[AccountingProvider.ZOHO], client)
