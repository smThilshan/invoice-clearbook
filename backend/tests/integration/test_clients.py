from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from app.domain.enums import AccountingProvider
from app.services.clients import ClientError, add_client, browse, get_client, list_clients

pytestmark = pytest.mark.integration


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    root = tmp_path / "Cafe Al Noor"
    (root / "October 2026").mkdir(parents=True)
    return root


def test_add_and_find_a_restaurant(session: Session, folder: Path) -> None:
    client = add_client(session, client_id="cafe-al-noor", name="  Cafe Al Noor ",
                        inbox_path=folder, accounting_provider=AccountingProvider.XERO)  # fmt: skip

    assert (client.name, client.inbox_path, client.accounting_provider) == (
        "Cafe Al Noor", str(folder), AccountingProvider.XERO
    )  # fmt: skip
    assert get_client(session, "cafe-al-noor") is client
    assert [c.client_id for c in list_clients(session)] == ["cafe-al-noor"]
    assert browse(client) == ["October 2026"]


@pytest.mark.parametrize(
    ("client_id", "inbox", "message"),
    [
        ("Cafe Al Noor", None, "lowercase"),  # spaces and capitals
        ("x", None, "lowercase"),  # too short
        ("cafe-al-noor", Path("relative/folder"), "absolute"),
        ("cafe-al-noor", Path("/Volumes/not-mounted/Cafe"), "not reachable"),
    ],
)
def test_invalid_registrations_are_refused(
    session: Session, folder: Path, client_id: str, inbox: Path | None, message: str
) -> None:
    with pytest.raises(ClientError, match=message):
        add_client(session, client_id=client_id, name="Cafe", inbox_path=inbox or folder)


def test_a_restaurant_is_registered_once(session: Session, folder: Path) -> None:
    add_client(session, client_id="cafe-al-noor", name="Cafe Al Noor", inbox_path=folder)

    with pytest.raises(ClientError, match="already exists"):
        add_client(session, client_id="cafe-al-noor", name="Again", inbox_path=folder)


def test_inactive_restaurants_are_not_offered(session: Session, folder: Path) -> None:
    client = add_client(session, client_id="cafe-al-noor", name="Cafe Al Noor", inbox_path=folder)
    client.is_active = False
    session.flush()

    assert get_client(session, "cafe-al-noor") is None
    assert list_clients(session) == []
