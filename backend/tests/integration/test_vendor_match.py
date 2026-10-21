"""Supplier match against the fictional F&B sample master data (infra/samples/master_data)."""

from typing import Any

import pytest
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.adapters.accounting.manual import ManualAccountingSystem
from app.domain.enums import AccountingProvider, DocumentType
from app.domain.invoice import Invoice
from app.models import Contact
from app.pipeline.verify.vendor_match import MatchMethod, VendorMatch, match_vendor
from app.services.master_data import import_master_data
from tests.integration.factories import CLIENT
from tests.test_manual_master_data import SAMPLES

pytestmark = pytest.mark.integration

AL_WAHA_TRN = "100999000000001"  # samples: Al Waha Hypermarket LLC


@pytest.fixture(autouse=True)
def suppliers(session: Session) -> None:
    samples = ManualAccountingSystem.from_directory(SAMPLES)
    import_master_data(session, client_id=CLIENT, source=samples)


def contact(session: Session, name: str) -> Contact:
    return session.scalars(select(Contact).where(Contact.name == name)).one()


def match(session: Session, client_id: str = CLIENT, **fields: Any) -> VendorMatch:
    invoice = Invoice(document_type=fields.pop("document_type", DocumentType.BILL), **fields)
    return match_vendor(session, invoice, client_id=client_id,
                        provider=AccountingProvider.MANUAL, min_similarity=0.6,
                        suggest_similarity=0.35)  # fmt: skip


def test_trn_match_wins_whatever_the_name(session: Session) -> None:
    result = match(session, vendor_name="Totally Different Name", vendor_trn="100-9990-0000-0001")

    assert result.method is MatchMethod.TRN
    assert result.contact_id == contact(session, "Al Waha Hypermarket LLC").id
    assert result.issues == []


@pytest.mark.parametrize(
    "printed", ["AL WAHA HYPERMARKET L.L.C.", "Al Waha Hypermarket", "al-waha hypermarket llc"]
)
def test_name_match_across_spellings(session: Session, printed: str) -> None:
    result = match(session, vendor_name=printed)

    assert result.method is MatchMethod.NAME
    assert result.contact_id == contact(session, "Al Waha Hypermarket LLC").id
    assert result.issues == []


def test_close_but_not_certain_name_is_a_possible_match(session: Session) -> None:
    result = match(session, vendor_name="Waha Market")

    assert result.contact_id is None
    [issue] = result.issues
    assert issue.code == "vendor_possible_match"
    assert "Al Waha Hypermarket LLC" in issue.message


def test_unknown_supplier_is_new(session: Session) -> None:
    result = match(session, vendor_name="Zebra Industrial Solutions")

    assert (result.contact_id, result.method) == (None, MatchMethod.NONE)
    [issue] = result.issues
    assert issue.code == "vendor_new"


def test_name_match_with_a_different_trn_is_warned(session: Session) -> None:
    result = match(session, vendor_name="Al Waha Hypermarket LLC", vendor_trn="100888000000009")

    assert result.method is MatchMethod.NAME
    [issue] = result.issues
    assert (issue.code, issue.field) == ("vendor_trn_differs", "vendor_trn")


def test_supplier_without_trn_on_file_matches_by_name(session: Session) -> None:
    result = match(session, vendor_name="Corner Grocery", vendor_trn="100777000000001")  # unknown

    assert result.contact_id == contact(session, "Corner Grocery").id
    assert result.issues == []  # nothing on file to disagree with


def test_inactive_suppliers_are_not_candidates(session: Session) -> None:
    session.execute(update(Contact).where(Contact.name == "Al Waha Hypermarket LLC")
                    .values(is_active=False))  # fmt: skip

    result = match(session, vendor_name="Al Waha Hypermarket LLC", vendor_trn=AL_WAHA_TRN)

    assert result.contact_id is None


def test_other_clients_suppliers_never_match(session: Session) -> None:
    result = match(session, client_id="other-client", vendor_name="Al Waha Hypermarket LLC",
                   vendor_trn=AL_WAHA_TRN)  # fmt: skip

    assert result.contact_id is None
    assert [issue.code for issue in result.issues] == ["vendor_new"]


def test_missing_name_and_non_postables_are_left_to_other_checks(session: Session) -> None:
    assert match(session).issues == []
    assert match(session, document_type=DocumentType.NON_POSTABLE, vendor_name="Zebra").issues == []
