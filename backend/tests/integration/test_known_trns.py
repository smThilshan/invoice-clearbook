"""Decision 104: the supplier TRNs already confirmed for a client, which make a skipped TRN
line on a scan a warning. All values are made up."""

from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from app.domain.enums import AccountingProvider, DocumentStatus
from app.models import Contact
from app.pipeline.verify.step import _known_trns
from tests.integration.factories import CLIENT, make_client, make_document

pytestmark = pytest.mark.integration

APPROVED, POSTED, IN_REVIEW, SYNCED = (
    "100111222333003", "100444555666003", "100777888999003", "100123456789003",
)  # fmt: skip


def test_known_trns_are_on_approved_documents_and_synced_suppliers(
    session: Session, tmp_path: Path
) -> None:
    make_client(session, tmp_path / "Cafe Al Noor")
    make_client(session, tmp_path / "Other", client_id="other-company")
    for status, trn in [
        (DocumentStatus.APPROVED, APPROVED),
        (DocumentStatus.POSTED, f"{POSTED[:5]} {POSTED[5:]}"),  # as typed, with a space
        (DocumentStatus.IN_REVIEW, IN_REVIEW),  # nobody checked it yet
    ]:
        make_document(session, status=status, invoice={"vendor_trn": trn})
    make_document(session, client_id="other-company", status=DocumentStatus.POSTED,
                  invoice={"vendor_trn": "100999999999003"})  # fmt: skip
    session.add_all([
        Contact(client_id=CLIENT, provider=AccountingProvider.ZOHO, external_id="v1",
                name="Made-up Dairy LLC", tax_number=SYNCED, is_supplier=True),
        Contact(client_id=CLIENT, provider=AccountingProvider.ZOHO, external_id="v2",
                name="Made-up Shop", tax_number=None, is_supplier=True),
    ])  # fmt: skip
    session.flush()

    assert _known_trns(session, CLIENT) == {APPROVED, POSTED, SYNCED}
