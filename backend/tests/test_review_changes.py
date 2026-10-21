"""Which fields an accountant's edit verifies (decision 24)."""

from datetime import date
from decimal import Decimal

from app.domain.enums import DocumentType
from app.domain.invoice import Invoice, Line
from app.services.review import CONFIRMABLE, changed_fields

BILL = Invoice(
    document_type=DocumentType.BILL,
    invoice_number="INV-1",
    invoice_date=date(2026, 10, 15),
    total=Decimal("1050.00"),
    lines=[Line(description="Rice", amount=Decimal("1050.00"))],
    evidence={"total": "Total AED 1,050.00"},
)


def test_unchanged_invoice_verifies_nothing() -> None:
    assert changed_fields(BILL, BILL.model_copy()) == set()


def test_amounts_compare_by_value_not_by_text() -> None:
    assert changed_fields(BILL, BILL.model_copy(update={"total": Decimal("1050")})) == set()


def test_edited_fields_are_reported_with_lines_as_one_field() -> None:
    edited = BILL.model_copy(
        update={
            "total": Decimal("1060.00"),
            "vendor_trn": "100999000000001",
            "lines": [Line(description="Rice", amount=Decimal("1050.00"), account_code="5000")],
        }
    )

    assert changed_fields(BILL, edited) == {"total", "vendor_trn", "lines"}


def test_evidence_never_counts_as_an_edit() -> None:
    assert changed_fields(BILL, BILL.model_copy(update={"evidence": {}})) == set()
    assert "evidence" not in CONFIRMABLE


def test_entered_by_hand_verifies_every_filled_field() -> None:
    assert changed_fields(None, BILL) == {
        "document_type", "invoice_number", "invoice_date", "total", "lines"
    }  # fmt: skip
