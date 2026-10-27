"""Review API (task 4.2): queue, document detail and pages, edit + re-check, reject, master data."""

import uuid
from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.storage.keys import page_image_key
from app.adapters.storage.local import LocalStorage
from app.api.documents import get_enqueue_posting, get_review_llm, get_review_storage
from app.core.config import Settings, get_settings
from app.core.db import get_session
from app.domain.enums import AccountingProvider, DocumentType, QueueCategory
from app.domain.enums import DocumentStatus as S
from app.main import create_app
from app.models import AuditLog, Client, Contact, Document
from tests.integration.factories import CLIENT, make_client, make_run
from tests.integration.test_coding import FakeCoder
from tests.integration.test_verify import (  # noqa: F401  (fixtures)
    PRINTED,
    TODAY,
    bill,
    extracted,
    master_data,
    storage,
    verify,
)

pytestmark = pytest.mark.integration

DEFAULT = "INV-00291"


class FixedDay(Settings):
    def business_today(self) -> date:
        return TODAY


@pytest.fixture
def api(session: Session, storage: LocalStorage, tmp_path: Path) -> Iterator[TestClient]:  # noqa: F811
    make_client(session, tmp_path / "Cafe Al Noor")
    settings = FixedDay(_env_file=None, app_env="test")
    app = create_app(settings)
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_review_storage] = lambda: storage
    app.dependency_overrides[get_settings] = lambda: settings
    app.state.posting_asked = []  # outbox ids the worker was asked to post (task 5.1)
    app.dependency_overrides[get_enqueue_posting] = lambda: app.state.posting_asked.append
    app.state.coder = FakeCoder({"accounts": []})  # the model's account suggestions
    app.dependency_overrides[get_review_llm] = lambda: lambda: app.state.coder
    with TestClient(app) as test_client:
        yield test_client


def in_review(
    session: Session,
    storage: LocalStorage,  # noqa: F811
    number: str = DEFAULT,
    **changes: Any,
) -> Document:
    """A verified document printed with `number`; `changes` are what the model got wrong."""
    doc = extracted(session, storage, bill(invoice_number=number, **changes),
                    PRINTED.replace(DEFAULT, number))  # fmt: skip
    verify(session, storage, doc)
    return doc


def ok(response: Any, code: int = 200) -> Any:
    assert response.status_code == code, response.text
    return response.json()


def patch(api: TestClient, doc: Document, confirmed: tuple[str, ...] = (), **changes: Any) -> Any:
    invoice = ok(api.get(f"/documents/{doc.id}"))["invoice"]
    body = {"invoice": {**invoice, **changes}, "confirmed": list(confirmed)}
    return api.patch(f"/documents/{doc.id}", json=body)


def issue_codes(body: dict[str, Any]) -> set[str]:
    return {issue["code"] for issue in body["issues"]}


def moves(session: Session, doc: Document) -> list[tuple[S | None, S, str | None]]:
    rows = session.scalars(
        select(AuditLog).where(AuditLog.document_id == doc.id).order_by(AuditLog.id)
    )
    return [(row.from_status, row.to_status, row.reason) for row in rows]


# --- queue -------------------------------------------------------------------------------


def test_queue_puts_exception_first_then_check_then_ready(
    api: TestClient,
    session: Session,
    storage: LocalStorage,  # noqa: F811
) -> None:
    ready = in_review(session, storage, "INV-00301")
    check = in_review(session, storage, "INV-00302", due_date=date(2026, 11, 20))  # not printed
    exception = in_review(session, storage, "INV-00303", total=Decimal("1060.00"))

    body = ok(api.get("/documents", params={"client_id": CLIENT}))

    assert body["total"] == 3
    assert [d["document_id"] for d in body["documents"]] == [
        str(exception.id), str(check.id), str(ready.id)
    ]  # fmt: skip
    first = body["documents"][0]
    assert (first["queue"], first["file"], first["invoice_number"]) == (
        "EXCEPTION", "inv.pdf", "INV-00303"
    )  # fmt: skip
    assert first["errors"] >= 1 and Decimal(first["total"]) == Decimal("1060.00")


def test_queue_filters_and_pages(
    api: TestClient,
    session: Session,
    storage: LocalStorage,  # noqa: F811
) -> None:
    ready = in_review(session, storage, "INV-00301")
    in_review(session, storage, "INV-00303", total=Decimal("1060.00"))

    only_ready = ok(api.get("/documents", params={"client_id": CLIENT, "queue": "READY"}))
    by_run = ok(api.get("/documents", params={"client_id": CLIENT, "run_id": str(ready.run_id)}))
    second = ok(api.get("/documents", params={"client_id": CLIENT, "limit": 1, "offset": 1}))

    assert [d["document_id"] for d in only_ready["documents"]] == [str(ready.id)]
    assert [d["document_id"] for d in by_run["documents"]] == [str(ready.id)]
    assert (second["total"], [d["document_id"] for d in second["documents"]]) == (
        2,
        [str(ready.id)],
    )
    assert (
        ok(api.get("/documents", params={"client_id": CLIENT, "status": "REJECTED"}))["documents"]
        == []
    )


def test_queue_rows_say_which_folder_the_file_is_in(
    api: TestClient,
    session: Session,
    storage: LocalStorage,  # noqa: F811
) -> None:
    doc = in_review(session, storage)

    [row] = ok(api.get("/documents", params={"client_id": CLIENT}))["documents"]

    assert row["folder"] == doc.source_path.rsplit("/", 1)[0]
    assert row["file"] == doc.source_path.rsplit("/", 1)[1]


def test_runs_that_took_in_documents_are_offered_for_the_filter(
    api: TestClient,
    session: Session,
    storage: LocalStorage,  # noqa: F811
) -> None:
    first = in_review(session, storage, "INV-00301")
    second = in_review(session, storage, "INV-00303", total=Decimal("1060.00"))
    make_run(session)  # took in nothing: not offered

    runs = ok(api.get(f"/clients/{CLIENT}/runs"))

    assert {r["run_id"] for r in runs} == {str(first.run_id), str(second.run_id)}
    assert [r["documents"] for r in runs] == [1, 1]
    assert api.get("/clients/nobody/runs").status_code == 404


def test_queue_of_unknown_client_is_not_found(api: TestClient) -> None:
    assert api.get("/documents", params={"client_id": "nobody"}).status_code == 404


# --- one document and its pages --------------------------------------------------------------


def test_document_detail_has_invoice_issues_and_coding(
    api: TestClient,
    session: Session,
    storage: LocalStorage,  # noqa: F811
) -> None:
    doc = in_review(session, storage)

    body = ok(api.get(f"/documents/{doc.id}"))

    assert (body["status"], body["queue"], body["page_count"]) == ("IN_REVIEW", "READY", 1)
    assert body["invoice"]["invoice_number"] == DEFAULT
    assert Decimal(body["invoice"]["total"]) == Decimal("1050.00")
    assert [line["account_code"] for line in body["invoice"]["lines"]] == ["5000", "5000"]
    assert body["coding"][0]["account_source"] == "supplier_history"
    assert (body["issues"], body["verified_fields"]) == ([], [])
    assert body["source_path"] == "Purchase/2026-10-20/inv.pdf"


def test_unknown_document_is_not_found(api: TestClient) -> None:
    assert api.get(f"/documents/{uuid.uuid4()}").status_code == 404


def test_page_image_and_text_with_boxes(
    api: TestClient,
    session: Session,
    storage: LocalStorage,  # noqa: F811
) -> None:
    doc = in_review(session, storage)
    storage.put(page_image_key(CLIENT, doc.id, 1), b"\x89PNG fake")

    image = api.get(f"/documents/{doc.id}/pages/1/image")
    text = ok(api.get(f"/documents/{doc.id}/pages/1/text"))

    assert (image.status_code, image.headers["content-type"], image.content) == (
        200, "image/png", b"\x89PNG fake"
    )  # fmt: skip
    assert (text["page"], text["source"]) == (1, "text_layer")
    assert text["spans"][0]["text"] == "TAX INVOICE"
    assert len(text["spans"][0]["box"]) == 4


def test_pages_outside_the_document_or_missing_are_not_found(
    api: TestClient,
    session: Session,
    storage: LocalStorage,  # noqa: F811
) -> None:
    doc = in_review(session, storage)

    assert api.get(f"/documents/{doc.id}/pages/2/text").status_code == 404
    assert api.get(f"/documents/{doc.id}/pages/0/image").status_code == 404
    assert api.get(f"/documents/{doc.id}/pages/1/image").status_code == 404  # never rendered


# --- edit and re-check -----------------------------------------------------------------------


def test_corrected_total_rechecks_to_ready_and_is_human_verified(
    api: TestClient,
    session: Session,
    storage: LocalStorage,  # noqa: F811
) -> None:
    doc = in_review(session, storage, total=Decimal("1060.00"))
    assert doc.queue is QueueCategory.EXCEPTION

    body = ok(patch(api, doc, total="1050.00"))

    assert (body["queue"], body["issues"], body["verified_fields"]) == ("READY", [], ["total"])
    assert body["status"] == "IN_REVIEW"
    assert moves(session, doc)[-1][1] is S.IN_REVIEW  # an edit is not a status change


def test_confirmed_value_is_no_longer_grounded(
    api: TestClient,
    session: Session,
    storage: LocalStorage,  # noqa: F811
) -> None:
    doc = in_review(session, storage, due_date=date(2026, 11, 20))  # printed as payment terms
    assert doc.queue is QueueCategory.CHECK

    body = ok(patch(api, doc, confirmed=("due_date",)))

    assert (body["queue"], body["verified_fields"]) == ("READY", ["due_date"])


def test_rules_still_apply_to_verified_values(
    api: TestClient,
    session: Session,
    storage: LocalStorage,  # noqa: F811
) -> None:
    doc = in_review(session, storage)

    body = ok(patch(api, doc, total="1060.00"))  # typed, but lines and VAT do not add up

    assert body["queue"] == "EXCEPTION"
    assert "not_on_document" not in issue_codes(body)
    assert body["verified_fields"] == ["total"]


def test_new_number_takes_a_new_business_key_and_duplicates_are_caught(
    api: TestClient,
    session: Session,
    storage: LocalStorage,  # noqa: F811
) -> None:
    first = in_review(session, storage, "INV-00301")
    second = in_review(session, storage, "INV-00302")

    body = ok(patch(api, second, invoice_number="INV-00301"))

    assert body["queue"] == "EXCEPTION" and "duplicate" in issue_codes(body)
    assert (first.business_key, second.business_key) == ("N:301", None)

    ok(patch(api, second, invoice_number="INV-00399"))
    assert second.business_key == "N:399"


def test_evidence_from_the_model_is_kept(
    api: TestClient,
    session: Session,
    storage: LocalStorage,  # noqa: F811
) -> None:
    doc = in_review(session, storage, evidence={"total": "Total AED 1,050.00"})

    body = ok(patch(api, doc, evidence={"total": "made up"}))

    assert body["invoice"]["evidence"] == {"total": "Total AED 1,050.00"}
    assert body["verified_fields"] == []


def test_marking_non_postable_parks_and_a_postable_type_brings_it_back(
    api: TestClient,
    session: Session,
    storage: LocalStorage,  # noqa: F811
) -> None:
    doc = in_review(session, storage)

    parked = ok(patch(api, doc, document_type="NON_POSTABLE"))
    back = ok(patch(api, doc, document_type="BILL"))

    assert (parked["status"], parked["document_type"]) == ("NON_POSTABLE", "NON_POSTABLE")
    assert (back["status"], back["queue"], back["document_type"]) == ("IN_REVIEW", "READY", "BILL")
    assert moves(session, doc)[-2:] == [
        (S.IN_REVIEW, S.NON_POSTABLE, "marked non-postable in review"),
        (S.NON_POSTABLE, S.IN_REVIEW, "type changed to BILL in review"),
    ]
    assert doc.document_type is DocumentType.BILL


def test_a_type_changed_from_non_postable_gets_its_lines_coded(
    api: TestClient,
    session: Session,
    storage: LocalStorage,  # noqa: F811
) -> None:
    """Verify does not code a document the model called non-postable; choosing a postable
    type in review does it then (supplier history here: no model call)."""
    doc = in_review(session, storage, document_type=DocumentType.NON_POSTABLE)
    assert {line["account_code"] for line in doc.invoice["lines"]} == {None}  # type: ignore[index]

    body = ok(patch(api, doc, document_type="BILL"))

    assert [(line["account_code"], line["tax_type"]) for line in body["invoice"]["lines"]] == [
        ("5000", "STANDARD_5"), ("5000", "STANDARD_5"),
    ]  # fmt: skip
    assert body["coding"][0]["account_source"] == "supplier_history"
    assert "account_missing" not in issue_codes(body)
    assert api.app.state.coder.requests == []  # type: ignore[attr-defined]


def test_coding_after_a_type_change_keeps_what_the_accountant_chose(
    api: TestClient,
    session: Session,
    storage: LocalStorage,  # noqa: F811
) -> None:
    doc = in_review(session, storage, document_type=DocumentType.NON_POSTABLE,
                    vendor_trn=None)  # no supplier history: the model suggests  # fmt: skip
    api.app.state.coder = FakeCoder({"accounts": ["5040", "5040"]})  # type: ignore[attr-defined]
    lines = ok(api.get(f"/documents/{doc.id}"))["invoice"]["lines"]
    lines[0] |= {"account_code": "5000", "tax_type": "ZERO"}

    body = ok(patch(api, doc, document_type="RECEIPT", lines=lines))

    assert [(line["account_code"], line["tax_type"]) for line in body["invoice"]["lines"]] == [
        ("5000", "ZERO"), ("5040", "STANDARD_5"),
    ]  # fmt: skip
    assert [c["account_source"] for c in body["coding"]] == ["none", "model"]


def test_other_edits_never_ask_the_model(
    api: TestClient,
    session: Session,
    storage: LocalStorage,  # noqa: F811
) -> None:
    doc = in_review(session, storage, vendor_trn=None)
    api.app.state.coder = FakeCoder(fail=True)  # type: ignore[attr-defined]
    lines = ok(api.get(f"/documents/{doc.id}"))["invoice"]["lines"]
    lines[0]["account_code"] = None

    body = ok(patch(api, doc, document_type="RECEIPT", lines=lines))

    assert body["invoice"]["lines"][0]["account_code"] is None
    assert "account_missing" in issue_codes(body)
    assert api.app.state.coder.requests == []  # type: ignore[attr-defined]


def test_unknown_confirmed_field_or_invalid_invoice_is_rejected(
    api: TestClient,
    session: Session,
    storage: LocalStorage,  # noqa: F811
) -> None:
    doc = in_review(session, storage)

    assert patch(api, doc, confirmed=("evidence",)).status_code == 422
    assert patch(api, doc, invented_field="x").status_code == 422  # schema forbids extras
    assert patch(api, doc, total="abc").status_code == 422
    assert ok(api.get(f"/documents/{doc.id}"))["verified_fields"] == []


# --- reject ----------------------------------------------------------------------------------


def test_reject_records_the_reason_and_releases_the_business_key(
    api: TestClient,
    session: Session,
    storage: LocalStorage,  # noqa: F811
) -> None:
    doc = in_review(session, storage)

    body = ok(api.post(f"/documents/{doc.id}/reject",
                       json={"reason": "DUPLICATE", "note": "  resent by email  "}))  # fmt: skip

    assert body["status"] == "REJECTED"
    last = session.scalars(
        select(AuditLog).where(AuditLog.document_id == doc.id).order_by(AuditLog.id.desc())
    ).first()
    assert last is not None
    assert (last.to_status, last.reason, last.actor) == (S.REJECTED, "DUPLICATE", "workbench")
    assert last.details == {"note": "resent by email"}
    assert in_review(session, storage).queue is QueueCategory.READY  # key free again


def test_reject_needs_a_note_for_other_and_a_document_in_review(
    api: TestClient,
    session: Session,
    storage: LocalStorage,  # noqa: F811
) -> None:
    doc = in_review(session, storage)

    assert api.post(f"/documents/{doc.id}/reject", json={"reason": "OTHER"}).status_code == 422
    assert api.post(f"/documents/{doc.id}/reject", json={"reason": "NOPE"}).status_code == 422
    ok(patch(api, doc, document_type="NON_POSTABLE"))
    assert api.post(f"/documents/{doc.id}/reject", json={"reason": "UNREADABLE"}).status_code == 409


def test_rejected_document_cannot_be_edited(
    api: TestClient,
    session: Session,
    storage: LocalStorage,  # noqa: F811
) -> None:
    doc = in_review(session, storage)
    ok(api.post(f"/documents/{doc.id}/reject", json={"reason": "UNREADABLE"}))

    response = api.patch(f"/documents/{doc.id}",
                         json={"invoice": {"document_type": "BILL"}})  # fmt: skip

    assert response.status_code == 409


# --- master data and coding options ----------------------------------------------------------

CONTACTS = "name,tax_number,is_supplier\nGulf Fresh Produce Trading LLC,,yes\n"


def test_master_data_upload_imports_the_files_sent(api: TestClient, session: Session) -> None:
    body = ok(api.post(f"/clients/{CLIENT}/master-data",
                       files={"contacts": ("contacts.csv", CONTACTS, "text/csv")}))  # fmt: skip

    assert (body["accounts"], body["tax_rates"]) == (None, None)
    assert body["contacts"]["created"] == 1
    names = session.scalars(select(Contact.name).where(Contact.client_id == CLIENT))
    assert "Gulf Fresh Produce Trading LLC" in set(names)


def test_master_data_with_problems_changes_nothing(api: TestClient, session: Session) -> None:
    before = len(session.scalars(select(Contact).where(Contact.client_id == CLIENT)).all())
    bad = CONTACTS + ",,yes\n"  # second row has no name

    response = api.post(f"/clients/{CLIENT}/master-data",
                        files={"contacts": ("contacts.csv", bad, "text/csv")})  # fmt: skip

    assert response.status_code == 422
    assert response.json()["detail"]["problems"] == ["contacts.csv row 3: 'name' is required"]
    assert len(session.scalars(select(Contact).where(Contact.client_id == CLIENT)).all()) == before


def test_master_data_upload_refusals(api: TestClient, session: Session, tmp_path: Path) -> None:
    session.add(Client(client_id="xero-cafe", name="Xero Cafe", inbox_path=str(tmp_path),
                       accounting_provider=AccountingProvider.XERO))  # fmt: skip
    session.flush()
    upload = {"contacts": ("contacts.csv", CONTACTS, "text/csv")}

    assert api.post("/clients/xero-cafe/master-data", files=upload).status_code == 409
    assert api.post("/clients/nobody/master-data", files=upload).status_code == 404
    assert api.post(f"/clients/{CLIENT}/master-data").status_code == 422
    not_utf8 = {"contacts": ("contacts.csv", b"\xff\xfename\n", "text/csv")}
    assert api.post(f"/clients/{CLIENT}/master-data", files=not_utf8).status_code == 422


def test_coding_options_are_the_active_purchase_accounts_and_mapped_tax_types(
    api: TestClient,
) -> None:
    body = ok(api.get(f"/clients/{CLIENT}/coding-options"))

    assert "5000" in {a["code"] for a in body["accounts"]}
    assert {a["account_class"] for a in body["accounts"]} <= {"EXPENSE", "ASSET"}
    assert "STANDARD_5" in {t["tax_type"] for t in body["tax_types"]}


# --- locate (task 4.6) -----------------------------------------------------------------------


def test_locate_returns_where_a_value_is_printed(
    api: TestClient,
    session: Session,
    storage: LocalStorage,  # noqa: F811
) -> None:
    doc = in_review(session, storage)

    def places(kind: str, value: str) -> Any:
        return ok(api.get(f"/documents/{doc.id}/locate", params={"kind": kind, "value": value}))[
            "places"
        ]

    [total] = places("amount", "1050")
    assert total["page"] == 1 and len(total["box"]) == 4
    assert places("date", "2026-10-15") != []
    assert places("trn", "100999000000001") != []
    assert places("amount", "1060.00") == []  # not printed
    assert api.get(f"/documents/{doc.id}/locate", params={"kind": "colour", "value": "x"}
                   ).status_code == 422  # fmt: skip
    assert api.get(f"/documents/{uuid.uuid4()}/locate",
                   params={"kind": "text", "value": "x"}).status_code == 404  # fmt: skip


def test_a_failed_document_is_retried_from_the_start(
    api: TestClient,
    session: Session,
    storage: LocalStorage,  # noqa: F811
) -> None:
    from app.api.documents import get_enqueue_step
    from app.domain.enums import DocumentStatus
    from tests.integration.factories import make_document

    queued: list[tuple[str, DocumentStatus]] = []
    api.app.dependency_overrides[get_enqueue_step] = lambda: lambda i, s: queued.append((i, s))  # type: ignore[attr-defined]
    failed = make_document(session, status=S.FAILED, failure_reason="read: cannot open PDF")
    ready = in_review(session, storage)

    body = ok(api.post(f"/documents/{failed.id}/retry"))

    assert (body["status"], body["failure_reason"]) == ("CAPTURED", None)
    assert queued == [(str(failed.id), DocumentStatus.CAPTURED)]
    assert api.post(f"/documents/{ready.id}/retry").status_code == 409


def test_queue_counts_every_category_searches_and_says_what_is_still_processing(
    api: TestClient,
    session: Session,
    storage: LocalStorage,  # noqa: F811
) -> None:
    from tests.integration.factories import make_document

    in_review(session, storage, "INV-00301")  # Ready
    in_review(session, storage, "INV-00303", total=Decimal("1060.00"))  # Exception
    make_document(session, status=S.READ)  # still being processed

    only_ready = ok(api.get("/documents", params={"client_id": CLIENT, "queue": "READY"}))
    found = ok(api.get("/documents", params={"client_id": CLIENT, "search": "inv-00303"}))
    nothing = ok(api.get("/documents", params={"client_id": CLIENT, "search": "100%"}))

    assert only_ready["counts"] == {"READY": 1, "EXCEPTION": 1}  # all categories, one shown
    assert only_ready["processing"] == 1
    assert [d["invoice_number"] for d in found["documents"]] == ["INV-00303"]
    assert nothing["total"] == 0  # "%" is plain text, not a wildcard
