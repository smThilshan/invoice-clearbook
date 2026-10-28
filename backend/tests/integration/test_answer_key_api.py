"""Answer key in review (decision 63): mark a document correct, without approving it."""

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.adapters.storage.local import LocalStorage
from app.domain.enums import DocumentStatus as S
from app.models import Extraction
from app.services import answer_key
from tests.integration.test_review_api import api, in_review, ok, patch  # noqa: F401  (fixtures)
from tests.integration.test_verify import bill, master_data, storage  # noqa: F401  (fixtures)

pytestmark = pytest.mark.integration


def test_marking_correct_keeps_the_version_as_saved_and_approves_nothing(
    api: TestClient,  # noqa: F811
    session: Session,
    storage: LocalStorage,  # noqa: F811
) -> None:
    doc = in_review(session, storage)
    assert ok(api.get(f"/documents/{doc.id}/answer-key")) == {"saved_at": None}
    ok(patch(api, doc, invoice_number="INV-00292"))  # the accountant's correction

    marked = ok(api.post(f"/documents/{doc.id}/answer-key"))

    assert marked["saved_at"] is not None
    assert ok(api.get(f"/documents/{doc.id}/answer-key")) == marked
    answer = answer_key.answer_of(storage, doc)
    assert answer is not None and answer.invoice_number == "INV-00292"
    session.refresh(doc)
    assert doc.status is S.IN_REVIEW  # not approved, nothing sent


def test_unmarking_removes_it(api: TestClient, session: Session, storage: LocalStorage) -> None:  # noqa: F811
    doc = in_review(session, storage)
    ok(api.post(f"/documents/{doc.id}/answer-key"))

    assert ok(api.delete(f"/documents/{doc.id}/answer-key")) == {"saved_at": None}
    assert answer_key.answer_of(storage, doc) is None


def test_replay_runs_the_saved_model_answer_through_todays_code(
    session: Session,
    storage: LocalStorage,  # noqa: F811
) -> None:
    doc = in_review(session, storage)
    model = bill(invoice_number="INV-00292")  # what the model read
    session.add(Extraction(client_id=doc.client_id, document_id=doc.id, attempt=1, model="m",
                           prompt_version="p", schema_version=1,
                           raw_output=model.model_dump_json(),
                           data=json.loads(model.model_dump_json())))  # fmt: skip
    session.flush()
    answer_key.save_answer(storage, doc)  # the answer: the number as printed (INV-00291)

    read = answer_key.replay(session, storage, doc)
    expected = answer_key.answer_of(storage, doc)

    assert expected is not None
    assert answer_key.differences(expected, read) == {"invoice_number": ("INV-00291", "INV-00292")}
