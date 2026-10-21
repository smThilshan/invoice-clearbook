from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy.orm import Session

from app.domain.enums import DocumentStatus as S
from app.domain.enums import DocumentType
from app.domain.invoice import Invoice
from app.models import Document
from app.pipeline.verify.duplicates import check_duplicates, claim_keys
from tests.integration.factories import make_document

pytestmark = pytest.mark.integration

TRN = "100999000000001"


def bill(**fields: Any) -> Invoice:
    values: dict[str, Any] = {
        "document_type": DocumentType.BILL,
        "vendor_name": "Al Waha Hypermarket LLC",
        "vendor_trn": TRN,
        "invoice_number": "INV-00291",
        "invoice_date": date(2026, 10, 15),
        "total": Decimal("1050.00"),
    }
    values.update(fields)
    return Invoice(**values)


def verified(session: Session, invoice: Invoice, status: S = S.IN_REVIEW) -> Document:
    """A document that already went through the duplicate check and holds its keys."""
    doc = make_document(
        session,
        status=status,
        document_type=invoice.document_type,
        invoice=invoice.model_dump(mode="json"),
    )
    assert claim_keys(session, doc, check_duplicates(session, doc, invoice))
    return doc


def new_document(session: Session, invoice: Invoice) -> Document:
    return make_document(session, status=S.EXTRACTED, document_type=invoice.document_type)


def test_first_document_claims_its_keys(session: Session) -> None:
    doc = verified(session, bill())

    assert (doc.vendor_key, doc.business_key) == (f"TRN:{TRN}", "N:291")


def test_same_bill_again_is_a_duplicate_error(session: Session) -> None:
    original = verified(session, bill())
    copy = new_document(session, bill(invoice_number="inv 291", vendor_name="AL WAHA L.L.C."))

    result = check_duplicates(session, copy, bill(invoice_number="inv 291"))

    [issue] = result.issues
    assert (issue.code, issue.field) == ("duplicate", "invoice_number")
    assert str(original.id) in issue.message
    assert result.business_key is None  # not claimable while the original is live


@pytest.mark.parametrize("released", [S.REJECTED, S.FAILED, S.DUPLICATE, S.VOIDED])
def test_released_documents_do_not_block(session: Session, released: S) -> None:
    verified(session, bill(), status=released)
    rerun = new_document(session, bill())

    result = check_duplicates(session, rerun, bill())

    assert result.issues == []
    assert claim_keys(session, rerun, result)


@pytest.mark.parametrize(
    "different",
    [
        {"invoice_number": "INV-00292"},
        {"vendor_trn": "100999000000002"},
        {"document_type": DocumentType.CREDIT_NOTE, "original_invoice_ref": "INV-00100"},
    ],
)
def test_other_number_supplier_or_type_is_not_a_duplicate(
    session: Session, different: dict[str, Any]
) -> None:
    verified(session, bill())
    other = bill(**different)

    issues = check_duplicates(session, new_document(session, other), other).issues

    assert "duplicate" not in {issue.code for issue in issues}


def test_receipts_without_number_use_date_and_total(session: Session) -> None:
    receipt = bill(document_type=DocumentType.RECEIPT, invoice_number=None,
                   total=Decimal("235.50"))  # fmt: skip
    verified(session, receipt)

    [issue] = check_duplicates(session, new_document(session, receipt), receipt).issues

    assert issue.code == "duplicate"


def test_two_copies_verified_at_once_only_one_claims_the_key(session: Session) -> None:
    first, second = new_document(session, bill()), new_document(session, bill())
    first_check = check_duplicates(session, first, bill())
    second_check = check_duplicates(session, second, bill())  # both saw no duplicate

    assert claim_keys(session, first, first_check)
    assert not claim_keys(session, second, second_check)  # the unique index decides
    assert (second.vendor_key, second.business_key) == (f"TRN:{TRN}", None)


# --- credit notes ---------------------------------------------------------------------------


def credit(**fields: Any) -> Invoice:
    return bill(document_type=DocumentType.CREDIT_NOTE, invoice_number="CN-7",
                original_invoice_ref="INV-00291", **fields)  # fmt: skip


def test_credit_note_within_original_passes(session: Session) -> None:
    verified(session, bill())
    note = credit(total=Decimal("-200.00"))

    assert check_duplicates(session, new_document(session, note), note).issues == []


def test_credit_note_above_original_is_an_error(session: Session) -> None:
    verified(session, bill())
    note = credit(total=Decimal("-1200.00"))

    [issue] = check_duplicates(session, new_document(session, note), note).issues

    assert (issue.code, issue.field) == ("credit_exceeds_original", "total")


def test_credit_note_original_not_in_our_records_is_a_warning(session: Session) -> None:
    note = credit(total=Decimal("-200.00"))

    [issue] = check_duplicates(session, new_document(session, note), note).issues

    assert issue.code == "credit_note_original_not_found"
    assert "accounting system" in issue.message
