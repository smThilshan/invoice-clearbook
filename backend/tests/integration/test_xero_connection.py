"""Connecting a client to Xero (task 5.3): state, encrypted tokens, renewal, disconnect, API."""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.adapters.accounting.base import (
    ContactRecord,
    LedgerAccountRecord,
    MasterData,
    TaxRateRecord,
)
from app.adapters.accounting.xero.auth import Tenant, Tokens
from app.core.config import Settings
from app.domain.enums import AccountClass, AccountingProvider, TaxType
from app.models import AccountingConnection, LedgerAccount
from app.pipeline.coding import load_chart
from app.services import accounting_connections as connections
from app.services.accounting_connections import AccountingConnectionError
from app.services.accounting_sync import sync_xero
from app.services.clients import get_client
from tests.integration.factories import CLIENT, make_client

pytestmark = pytest.mark.integration

SETTINGS = Settings(
    _env_file=None,
    app_env="test",
    xero_client_id="app-id",
    xero_client_secret="app-secret",
    token_key=Fernet.generate_key().decode(),
)
LATER = timedelta(minutes=30)


class FakeXero:
    """Xero's identity service, as the connection service uses it."""

    def __init__(self) -> None:
        self.refreshed: list[str] = []
        self.disconnected: list[str] = []

    def configured(self) -> bool:
        return True

    def authorize_url(self, state: str) -> str:
        return f"https://login.xero.com/identity/connect/authorize?state={state}"

    def exchange(self, code: str) -> Tokens:
        assert code == "code-1"
        return Tokens(
            "access-1", "refresh-1", datetime.now(UTC) + timedelta(minutes=30), "s", "event-2"
        )

    def refresh(self, refresh_token: str) -> Tokens:
        self.refreshed.append(refresh_token)
        return Tokens("access-2", "refresh-2", datetime.now(UTC) + LATER, "s", None)

    def tenants(self, access_token: str) -> list[Tenant]:
        return [Tenant("c1", "t1", "Other company", "event-1"),
                Tenant("c2", "t2", "Demo Company (Global)", "event-2")]  # fmt: skip

    def disconnect(self, access_token: str, connection_id: str) -> None:
        self.disconnected.append(connection_id)


@pytest.fixture
def xero(session: Session, tmp_path: Path) -> FakeXero:
    make_client(session, tmp_path / "Cafe Al Noor")
    return FakeXero()


def connect(session: Session, xero: FakeXero) -> AccountingConnection:
    client = get_client(session, CLIENT)
    assert client is not None
    url = connections.start_xero(SETTINGS, xero, client)  # type: ignore[arg-type]
    state = url.split("state=")[1]
    connections.finish_xero(session, SETTINGS, xero, state, "code-1")  # type: ignore[arg-type]
    found = connections.connection_of(session, CLIENT)
    assert found is not None
    return found


def test_connecting_stores_the_approved_organisation_with_encrypted_tokens(
    session: Session, xero: FakeXero
) -> None:
    connection = connect(session, xero)

    assert (connection.tenant_id, connection.tenant_name) == ("t2", "Demo Company (Global)")
    assert "access-1" not in connection.access_token  # stored encrypted
    assert "refresh-1" not in connection.refresh_token
    token = connections.access_token(session, SETTINGS, xero, CLIENT)  # type: ignore[arg-type]
    assert token == "access-1"


def test_a_sign_in_not_started_here_or_too_old_is_refused(
    session: Session, xero: FakeXero, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(AccountingConnectionError, match="not started from the Workbench"):
        connections.finish_xero(session, SETTINGS, xero, f"{CLIENT}.1.x.forged", "code-1")  # type: ignore[arg-type]

    state = connections._state(SETTINGS, CLIENT)
    monkeypatch.setattr(connections.time, "time", lambda: 9_999_999_999.0)
    with pytest.raises(AccountingConnectionError, match="took too long"):
        connections.finish_xero(session, SETTINGS, xero, state, "code-1")  # type: ignore[arg-type]


def test_an_access_about_to_expire_is_renewed(session: Session, xero: FakeXero) -> None:
    connection = connect(session, xero)
    connection.expires_at = datetime.now(UTC) + timedelta(seconds=30)

    token = connections.access_token(session, SETTINGS, xero, CLIENT)  # type: ignore[arg-type]

    assert (token, xero.refreshed) == ("access-2", ["refresh-1"])
    assert connection.expires_at > datetime.now(UTC) + timedelta(minutes=20)


def test_disconnecting_removes_it_here_and_in_xero(session: Session, xero: FakeXero) -> None:
    connect(session, xero)

    connections.disconnect(session, SETTINGS, xero, CLIENT)  # type: ignore[arg-type]

    assert connections.connection_of(session, CLIENT) is None
    assert xero.disconnected == ["c2"]


def test_without_a_token_key_connecting_says_how_to_fix_it(
    session: Session, xero: FakeXero
) -> None:
    client = get_client(session, CLIENT)
    no_key = Settings(_env_file=None, xero_client_id="app-id", xero_client_secret="app-secret")

    with pytest.raises(AccountingConnectionError, match="make secret-key"):
        connections.start_xero(no_key, xero, client)  # type: ignore[arg-type]


@pytest.fixture
def api(session: Session, xero: FakeXero) -> Iterator[TestClient]:
    from app.api.xero import get_xero_auth
    from app.core.config import get_settings
    from app.core.db import get_session
    from app.main import create_app

    app = create_app(SETTINGS)
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_settings] = lambda: SETTINGS
    app.dependency_overrides[get_xero_auth] = lambda: xero
    with TestClient(app, follow_redirects=False) as client:
        yield client


def test_the_browser_goes_to_xero_and_back_to_the_workbench(api: TestClient) -> None:
    start = api.get("/xero/connect", params={"client_id": CLIENT})
    state = start.headers["location"].split("state=")[1]
    back = api.get("/xero/callback", params={"code": "code-1", "state": state})
    status = api.get(f"/clients/{CLIENT}/accounting").json()

    assert start.status_code == 307 and start.headers["location"].startswith(
        "https://login.xero.com/"
    )
    assert back.status_code == 303
    assert back.headers["location"].startswith("http://127.0.0.1:5173/clients?client=")
    assert "connected=Demo+Company" in back.headers["location"]
    assert (status["connected"], status["organisation"]) == (True, "Demo Company (Global)")


def test_a_cancelled_xero_sign_in_comes_back_with_the_reason(api: TestClient) -> None:
    back = api.get("/xero/callback", params={"error": "access_denied"})

    assert "error=Xero+sign-in+was+not+completed" in back.headers["location"]


# --- Sync from Xero (task 5.4) ---------------------------------------------------------------


class FakeOrganisation:
    """One Xero organisation's master data, as XeroAccountingSystem returns it."""

    def __init__(self) -> None:
        self.accounts = [
            LedgerAccountRecord("a1", "400", "Advertising", AccountClass.EXPENSE, "INPUT"),
            LedgerAccountRecord("a2", "420", "Entertainment", AccountClass.EXPENSE, "INPUT"),
        ]
        self.tax_rates = [
            TaxRateRecord("INPUT", "INPUT", "Standard Rated Expenses", Decimal("5"),
                          TaxType.STANDARD_5),
            TaxRateRecord("OUTPUT", "OUTPUT", "Standard Rated Income", Decimal("5"), None),
        ]  # fmt: skip
        self.contacts = [ContactRecord("c1", "Made-up Supplier LLC", None, is_supplier=True)]
        self.asked: list[tuple[str, str]] = []

    def system(self, token: str, tenant_id: str) -> "FakeOrganisation":
        self.asked.append((token, tenant_id))
        return self

    provider = AccountingProvider.XERO

    def fetch_master_data(self) -> MasterData:
        return MasterData(self.accounts, self.tax_rates, self.contacts)


def test_syncing_brings_xero_master_data_and_switches_the_client_to_it(
    session: Session, xero: FakeXero
) -> None:
    connect(session, xero)
    organisation = FakeOrganisation()
    client = get_client(session, CLIENT)
    assert client is not None

    report = sync_xero(session, SETTINGS, xero, client, organisation.system)  # type: ignore[arg-type]

    assert organisation.asked == [("access-1", "t2")]
    assert (report.accounts.created, report.tax_rates.created, report.contacts.created) == (2, 2, 1)
    assert [(m.code, m.tax_type) for m in report.tax_mapping] == [
        ("INPUT", TaxType.STANDARD_5), ("OUTPUT", None)]  # fmt: skip
    assert client.accounting_provider is AccountingProvider.XERO
    chart = load_chart(session, client_id=CLIENT, provider=AccountingProvider.XERO)
    assert sorted(chart.accounts) == ["400", "420"]
    assert chart.tax_codes == {TaxType.STANDARD_5: "INPUT"}


def test_syncing_again_marks_what_was_removed_in_xero_inactive(
    session: Session, xero: FakeXero
) -> None:
    connect(session, xero)
    organisation = FakeOrganisation()
    client = get_client(session, CLIENT)
    sync_xero(session, SETTINGS, xero, client, organisation.system)  # type: ignore[arg-type]
    organisation.accounts = organisation.accounts[:1]

    report = sync_xero(session, SETTINGS, xero, client, organisation.system)  # type: ignore[arg-type]

    assert (report.accounts.unchanged, report.deactivated) == (1, 1)
    chart = load_chart(session, client_id=CLIENT, provider=AccountingProvider.XERO)
    assert sorted(chart.accounts) == ["400"]
    assert session.scalars(select(func.count()).select_from(LedgerAccount)).one() == 2  # kept


def test_sync_through_the_api_and_csv_import_is_then_refused(
    api: TestClient, session: Session, xero: FakeXero
) -> None:
    from app.api.xero import get_xero_system

    connect(session, xero)
    organisation = FakeOrganisation()
    api.app.dependency_overrides[get_xero_system] = lambda: organisation.system  # type: ignore[attr-defined]

    synced = api.post(f"/clients/{CLIENT}/accounting/sync")
    status = api.get(f"/clients/{CLIENT}/accounting").json()
    csv = api.post(f"/clients/{CLIENT}/master-data", files={"contacts": ("c.csv", b"x")})

    assert synced.status_code == 200, synced.text
    assert synced.json()["tax_mapping"][0] == {
        "code": "INPUT", "name": "Standard Rated Expenses", "tax_type": "STANDARD_5"}  # fmt: skip
    assert status["master_data_from"] == "XERO"
    assert csv.status_code == 409


def test_sync_without_a_connection_says_so(api: TestClient) -> None:
    answer = api.post(f"/clients/{CLIENT}/accounting/sync")

    assert answer.status_code == 409
    assert "not connected" in answer.json()["detail"]


def test_sending_waiting_bills_needs_xero_master_data_then_asks_the_worker(
    api: TestClient, session: Session, xero: FakeXero
) -> None:
    from app.api.xero import get_enqueue_sweep, get_xero_system

    asked: list[str] = []
    api.app.dependency_overrides[get_enqueue_sweep] = lambda: asked.append  # type: ignore[attr-defined]
    api.app.dependency_overrides[get_xero_system] = lambda: FakeOrganisation().system  # type: ignore[attr-defined]
    connect(session, xero)

    before_sync = api.post(f"/clients/{CLIENT}/accounting/send")
    api.post(f"/clients/{CLIENT}/accounting/sync")
    after_sync = api.post(f"/clients/{CLIENT}/accounting/send")

    assert before_sync.status_code == 409
    assert after_sync.status_code == 200 and after_sync.json()["waiting"] == 0
    assert asked == [CLIENT]
