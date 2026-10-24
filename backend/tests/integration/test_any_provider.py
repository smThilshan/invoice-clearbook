"""Nothing outside a provider's folder assumes Xero (decision 43): the same connect, sync, post
and status, with the registry's entry renamed "Fakebooks", under its own web address."""

from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.adapters.accounting.registry import Registry
from app.domain.enums import AccountingProvider, DocumentStatus
from tests.integration.factories import CLIENT, make_client
from tests.integration.test_posting import FakePosting, approved, send
from tests.integration.test_xero_connection import SETTINGS, FakeXero, registry

pytestmark = pytest.mark.integration


def fakebooks(xero: FakeXero) -> Registry:
    entry = registry(xero)[AccountingProvider.XERO]
    return {entry.provider: replace(entry, name="Fakebooks", slug="fakebooks",
                                    bills_where="Purchases, To approve")}  # fmt: skip


@pytest.fixture
def api(session: Session, tmp_path: Path) -> Iterator[tuple[TestClient, FakeXero]]:
    from app.api.accounting import get_enqueue_sweep, get_registry
    from app.core.config import get_settings
    from app.core.db import get_session
    from app.main import create_app

    make_client(session, tmp_path / "Cafe Al Noor")
    xero = FakeXero()
    app = create_app(SETTINGS)
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_settings] = lambda: SETTINGS
    app.dependency_overrides[get_registry] = lambda: fakebooks(xero)
    app.dependency_overrides[get_enqueue_sweep] = lambda: lambda client_id: None
    with TestClient(app, follow_redirects=False) as client:
        yield client, xero


def test_connect_sync_and_status_speak_of_the_registered_provider(
    api: tuple[TestClient, FakeXero],
) -> None:
    client, _ = api

    start = client.get("/fakebooks/connect", params={"client_id": CLIENT})
    state = start.headers["location"].split("state=")[1]
    back = client.get("/fakebooks/callback", params={"code": "code-1", "state": state})
    synced = client.post(f"/clients/{CLIENT}/accounting/sync")
    status = client.get(f"/clients/{CLIENT}/accounting").json()

    assert "system=Fakebooks" in back.headers["location"]
    assert synced.status_code == 200, synced.text
    assert (status["provider_name"], status["bills_where"]) == (
        "Fakebooks",
        "Purchases, To approve",
    )
    assert status["providers"][0]["connect_url"] == "/fakebooks/connect"
    assert client.get("/xero/connect", params={"client_id": CLIENT}).status_code == 404


def test_a_bill_sent_back_names_the_registered_provider(
    api: tuple[TestClient, FakeXero], session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, xero = api
    start = client.get("/fakebooks/connect", params={"client_id": CLIENT})
    state = start.headers["location"].split("state=")[1]
    client.get("/fakebooks/callback", params={"code": "code-1", "state": state})
    client.post(f"/clients/{CLIENT}/accounting/sync")
    doc, _ = approved(session)  # its account 429 is not in this provider's chart
    import tests.integration.test_posting as posting_tests

    monkeypatch.setattr(posting_tests, "registry", lambda x: fakebooks(xero))
    send(session, FakePosting())

    session.refresh(doc)
    assert doc.status is DocumentStatus.IN_REVIEW
    assert doc.failure_reason is not None
    assert doc.failure_reason.startswith("Not sent to Fakebooks: line 1: account 429 is not in "
                                         "Fakebooks's chart of accounts")  # fmt: skip
    status = client.get(f"/clients/{CLIENT}/accounting").json()
    assert status["posting"]["sent_back"] == 1
