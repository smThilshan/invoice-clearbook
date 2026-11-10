"""Decision 91: an admin deletes a removed company permanently. Every row and stored file of
that company goes, one proof row per document stays, and no other company is touched."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.adapters.storage.local import LocalStorage
from app.domain.enums import AccountClass, AccountingProvider, DocumentStatus, Role
from app.models import (
    AuditLog,
    Base,
    Client,
    Contact,
    Correction,
    Deletion,
    Extraction,
    LedgerAccount,
    Outbox,
    OutboxStatus,
    Run,
    RunStatus,
    TaxRate,
    VendorMapping,
)
from app.services.company_deletion import REASON
from tests.integration.factories import CLIENT, make_client, make_document
from tests.integration.test_xero_connection import SETTINGS, FakeXero, connect, registry
from tests.login import log_in

pytestmark = pytest.mark.integration

OTHER = "other-company"
NAME = f"Test {CLIENT}"  # make_client's name
CLIENT_TABLES = sorted(
    name
    for name, table in Base.metadata.tables.items()
    if "client_id" in table.columns and name != "deletions"
)


def fill(session: Session, client_id: str) -> None:
    """One row of the company in every table it has rows in; one document posted."""
    run = Run(client_id=client_id, direction="PURCHASE", status=RunStatus.COMPLETED)
    session.add(run)
    session.flush()
    for n, external_id in enumerate([None, f"xero-bill-{client_id}"]):
        document = make_document(
            session, client_id=client_id, run_id=run.id, status=DocumentStatus.APPROVED,
            external_id=external_id, sha256=f"{n}{client_id}".ljust(64, "0")[:64], page_count=1,
        )  # fmt: skip
        extraction = Extraction(client_id=client_id, document_id=document.id, attempt=1, model="m",
                                prompt_version="p", schema_version=1, raw_output="{}")  # fmt: skip
        session.add(extraction)
        session.flush()
        session.add_all([
            Correction(client_id=client_id, document_id=document.id, extraction_id=extraction.id,
                       field="total", corrected_by="tester"),
            AuditLog(client_id=client_id, document_id=document.id, actor="tester",
                     from_status=DocumentStatus.IN_REVIEW, to_status=DocumentStatus.APPROVED),
            Outbox(client_id=client_id, document_id=document.id, status=OutboxStatus.DONE),
        ])  # fmt: skip
    session.add_all([
        VendorMapping(client_id=client_id, vendor_key="made-up supplier"),
        LedgerAccount(client_id=client_id, provider=AccountingProvider.MANUAL, external_id="a1",
                      code="400", name="Purchases", account_class=AccountClass.EXPENSE),
        TaxRate(client_id=client_id, provider=AccountingProvider.MANUAL, external_id="t1",
                code="INPUT", name="Standard"),
        Contact(client_id=client_id, provider=AccountingProvider.MANUAL, external_id="c1",
                name="Made-up Supplier"),
    ])  # fmt: skip
    session.flush()


def rows(session: Session, client_id: str) -> dict[str, int]:
    return {
        name: session.scalar(
            select(func.count()).select_from(table).where(table.c.client_id == client_id)
        )
        or 0
        for name, table in Base.metadata.tables.items()
        if name in CLIENT_TABLES
    }


@pytest.fixture
def xero() -> FakeXero:
    return FakeXero()


@pytest.fixture
def store(tmp_path: Path) -> LocalStorage:
    storage = LocalStorage(tmp_path / "store")
    for client_id in (CLIENT, OTHER):
        storage.put(f"{client_id}/originals/{'a' * 64}.pdf", b"%PDF made-up")
        storage.put(f"{client_id}/pages/x/page-001.png", b"made-up")
    return storage


@pytest.fixture
def api(
    session: Session, tmp_path: Path, xero: FakeXero, store: LocalStorage
) -> Iterator[TestClient]:
    from app.api.accounting import get_registry
    from app.api.clients import get_company_storage
    from app.core.config import get_settings
    from app.core.db import get_session
    from app.main import create_app

    make_client(session, tmp_path / "Cafe Al Noor")
    make_client(session, tmp_path / "Other", client_id=OTHER)
    for client_id in (CLIENT, OTHER):
        fill(session, client_id)
    connect(session, xero)
    app = create_app(SETTINGS)
    log_in(app)
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_settings] = lambda: SETTINGS
    app.dependency_overrides[get_registry] = lambda: registry(xero)
    app.dependency_overrides[get_company_storage] = lambda: store
    with TestClient(app) as test_client:
        yield test_client


def removed(api: TestClient) -> TestClient:
    assert api.post(f"/clients/{CLIENT}/remove").status_code == 200
    return api


def test_every_table_with_company_rows_is_filled_here() -> None:
    """So the tests below prove every table is emptied: a new table must be filled in `fill`."""
    filled = sorted([
        "accounting_connections", "audit_log", "clients", "contacts", "corrections", "documents",
        "extractions", "ledger_accounts", "outbox", "runs", "tax_rates", "vendor_mappings",
    ])  # fmt: skip
    assert filled == CLIENT_TABLES


def test_the_admin_sees_what_would_go_before_deleting(api: TestClient) -> None:
    response = removed(api).get(f"/clients/{CLIENT}/deletion")

    assert response.status_code == 200, response.text
    assert response.json() == {
        "documents": 2, "posted": 1, "connected_to": "Xero", "blocked": None,
    }  # fmt: skip


def test_deleting_removes_every_row_and_file_of_that_company_only(
    api: TestClient, session: Session, xero: FakeXero, store: LocalStorage, tmp_path: Path
) -> None:
    before = rows(session, CLIENT)
    other_before = rows(session, OTHER)
    assert all(before.values()), before  # every table had a row to delete

    response = removed(api).post(f"/clients/{CLIENT}/delete", json={"name": NAME})

    assert response.status_code == 200, response.text
    assert response.json() == {"documents": 2, "files_removed": True}
    assert rows(session, CLIENT) == dict.fromkeys(CLIENT_TABLES, 0)
    assert rows(session, OTHER) == other_before
    assert xero.disconnected == []  # forgotten here only: a Xero sign-in may serve others
    assert not (tmp_path / "store" / CLIENT).exists()
    assert store.exists(f"{OTHER}/originals/{'a' * 64}.pdf")
    assert (tmp_path / "Cafe Al Noor").is_dir()  # its own source folder is never touched


def test_one_proof_row_per_document_keeps_what_was_posted(
    api: TestClient, session: Session
) -> None:
    removed(api).post(f"/clients/{CLIENT}/delete", json={"name": NAME})

    proof = session.scalars(select(Deletion).where(Deletion.client_id == CLIENT)).all()
    assert sorted((p.external_id or "") for p in proof) == ["", f"xero-bill-{CLIENT}"]
    assert {(p.rejected_reason, p.deleted_by) for p in proof} == {(REASON, "tester@missan.test")}
    assert {p.sha256[:1] for p in proof} == {"0", "1"}


def test_the_name_must_be_typed_exactly(api: TestClient, session: Session) -> None:
    before = rows(session, CLIENT)

    response = removed(api).post(f"/clients/{CLIENT}/delete", json={"name": "test client"})

    assert response.status_code == 409
    assert "does not match" in response.json()["detail"]
    assert rows(session, CLIENT) == before


def test_only_a_removed_company_can_be_deleted(api: TestClient, session: Session) -> None:
    assert api.get(f"/clients/{CLIENT}/deletion").status_code == 404
    assert api.post(f"/clients/{CLIENT}/delete", json={"name": NAME}).status_code == 404
    assert session.scalar(select(func.count()).select_from(Client)) == 2


def test_documents_still_waiting_to_be_sent_block_it(api: TestClient, session: Session) -> None:
    removed(api)
    session.execute(
        Outbox.__table__.update()
        .where(Outbox.client_id == CLIENT)
        .values(status=OutboxStatus.PENDING)
    )

    preview = api.get(f"/clients/{CLIENT}/deletion").json()
    response = api.post(f"/clients/{CLIENT}/delete", json={"name": NAME})

    assert "waiting to be sent" in preview["blocked"]
    assert response.status_code == 409
    assert rows(session, CLIENT)["documents"] == 2


def test_deleted_is_gone_and_its_name_can_be_used_again(api: TestClient) -> None:
    removed(api).post(f"/clients/{CLIENT}/delete", json={"name": NAME})

    assert api.post(f"/clients/{CLIENT}/delete", json={"name": NAME}).status_code == 404
    assert api.post(f"/clients/{CLIENT}/restore").status_code == 404
    assert api.post("/clients", json={"name": NAME}).status_code == 201


def test_only_an_admin_deletes(api: TestClient, session: Session) -> None:
    removed(api)
    log_in(api.app, Role.ACCOUNTANT)  # type: ignore[arg-type]

    assert api.get(f"/clients/{CLIENT}/deletion").status_code == 403
    assert api.post(f"/clients/{CLIENT}/delete", json={"name": NAME}).status_code == 403
    assert rows(session, CLIENT)["documents"] == 2


def test_files_that_cannot_be_removed_leave_the_rows_deleted_and_say_so(
    api: TestClient, session: Session, store: LocalStorage, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(prefix: str) -> None:
        raise PermissionError(prefix)

    monkeypatch.setattr(store, "delete_folder", refuse)

    response = removed(api).post(f"/clients/{CLIENT}/delete", json={"name": NAME})

    assert response.json() == {"documents": 2, "files_removed": False}
    assert rows(session, CLIENT)["clients"] == 0
