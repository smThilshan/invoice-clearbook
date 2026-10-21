"""Corrections (task 4.7): kept in step with every save, through the review API."""

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.storage.local import LocalStorage
from app.models import Correction, Document, Extraction
from tests.integration.test_review_api import (  # noqa: F401  (fixtures)
    api,
    in_review,
    ok,
    patch,
)
from tests.integration.test_verify import bill, extracted, master_data, storage, verify  # noqa: F401

pytestmark = pytest.mark.integration

MODEL = "qwen3-vl:4b-instruct"
PROMPT = "extract-v4"


def with_extraction(session: Session, storage: LocalStorage, **changes: Any) -> Document:  # noqa: F811
    """A verified document whose last extraction is the model's answer `bill(**changes)`."""
    answer = bill(**changes)
    doc = extracted(session, storage, answer)
    session.add(Extraction(client_id=doc.client_id, document_id=doc.id, attempt=1, model=MODEL,
                           prompt_version=PROMPT, schema_version=1, raw_output="{}",
                           data=json.loads(answer.model_dump_json())))  # fmt: skip
    session.flush()
    verify(session, storage, doc)
    return doc


def rows(session: Session, doc: Document) -> dict[str, Correction]:
    found = session.scalars(select(Correction).where(Correction.document_id == doc.id))
    return {row.field: row for row in found}


def test_a_changed_field_is_recorded_with_supplier_model_and_prompt(
    api: TestClient,  # noqa: F811
    session: Session,
    storage: LocalStorage,  # noqa: F811
) -> None:
    doc = with_extraction(session, storage)

    body = ok(patch(api, doc, total="1060.00"))

    [row] = rows(session, doc).values()
    assert (row.field, row.model_value, row.final_value) == ("total", "1050.00", "1060.00")
    assert (row.vendor_key, row.model, row.prompt_version, row.corrected_by) == (
        doc.vendor_key, MODEL, PROMPT, "workbench"
    )  # fmt: skip
    assert row.extraction_id is not None
    assert body["corrections"] == [
        {"field": "total", "model_value": "1050.00", "final_value": "1060.00"}
    ]


def test_changing_again_updates_and_changing_back_removes(
    api: TestClient,  # noqa: F811
    session: Session,
    storage: LocalStorage,  # noqa: F811
) -> None:
    doc = with_extraction(session, storage)

    ok(patch(api, doc, total="1060.00"))
    ok(patch(api, doc, total="1070.00"))
    assert [(r.field, r.final_value) for r in rows(session, doc).values()] == [("total", "1070.00")]

    body = ok(patch(api, doc, total="1050"))  # the model's value, written differently

    assert rows(session, doc) == {}
    assert body["corrections"] == []


def test_saving_without_changes_records_nothing(
    api: TestClient,  # noqa: F811
    session: Session,
    storage: LocalStorage,  # noqa: F811
) -> None:
    doc = with_extraction(session, storage)

    ok(patch(api, doc))

    assert rows(session, doc) == {}  # the suggested codes were kept: not corrections either


def test_line_values_and_suggested_codes_are_measured(
    api: TestClient,  # noqa: F811
    session: Session,
    storage: LocalStorage,  # noqa: F811
) -> None:
    doc = with_extraction(session, storage)
    lines = ok(api.get(f"/documents/{doc.id}"))["invoice"]["lines"]
    lines[0] = {**lines[0], "amount": "610.00", "account_code": "5010"}  # suggestion was 5000
    lines.append({"description": "Bags", "amount": "5.00"})

    ok(patch(api, doc, lines=lines))

    found = {field: (r.model_value, r.final_value) for field, r in rows(session, doc).items()}
    assert found == {
        "lines[0].amount": ("600.00", "610.00"),
        "lines[0].account_code": ("5000", "5010"),
        "lines[2].description": (None, "Bags"),
        "lines[2].amount": (None, "5.00"),
    }


def test_document_entered_by_hand_counts_every_value(
    api: TestClient,  # noqa: F811
    session: Session,
    storage: LocalStorage,  # noqa: F811
) -> None:
    doc = extracted(session, storage, None)  # the model's answer could not be read
    session.add(Extraction(client_id=doc.client_id, document_id=doc.id, attempt=1, model=MODEL,
                           prompt_version=PROMPT, schema_version=1, raw_output="sorry",
                           data=None, error="1 schema error(s)"))  # fmt: skip
    session.flush()
    verify(session, storage, doc)

    ok(api.patch(f"/documents/{doc.id}", json={"invoice": {
        "document_type": "BILL", "invoice_number": "INV-9", "total": "10.00"}}))  # fmt: skip

    found = {field: (r.model_value, r.final_value) for field, r in rows(session, doc).items()}
    assert found == {
        "document_type": (None, "BILL"),
        "invoice_number": (None, "INV-9"),
        "total": (None, "10.00"),
    }
