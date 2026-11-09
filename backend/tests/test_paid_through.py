"""The account an expense was paid through (decision 89): chosen by code, never by the model."""

from decimal import Decimal

from app.domain.enums import AccountClass, DocumentType
from app.domain.invoice import Invoice, Line
from app.pipeline.coding import Account, ChartOfAccounts
from app.pipeline.verify.paid_through import PaidThrough, choose_paid_through

PETTY_CASH, CARD, STAFF, OFFICE = "4600361", "4600362", "4600370", "4600400"
CHART = ChartOfAccounts(
    {
        PETTY_CASH: Account(PETTY_CASH, "Petty Cash", AccountClass.ASSET, pays="Cash"),
        CARD: Account(CARD, "Company Visa", AccountClass.LIABILITY, pays="Credit Card"),
        STAFF: Account(STAFF, "Employee Reimbursements", AccountClass.LIABILITY, pays="Other"),
        OFFICE: Account(OFFICE, "Office Supplies", AccountClass.EXPENSE),
    },
    {},
)


def expense(paid_through: str | None = None, kind: DocumentType = DocumentType.RECEIPT) -> Invoice:
    return Invoice(document_type=kind, total=Decimal("52.50"), paid_through=paid_through,
                   lines=[Line(description="Groceries", amount=Decimal("52.50"))])  # fmt: skip


def choose(
    invoice: Invoice, remembered: str | None = None, default: str | None = None, *verified: str
) -> tuple[str | None, list[str]]:
    chosen, issues = choose_paid_through(
        invoice, CHART, PaidThrough(remembered, default), verified=frozenset(verified)
    )
    return chosen.paid_through, [issue.code for issue in issues]


def test_the_suppliers_last_account_comes_before_the_clients_usual_one() -> None:
    assert choose(expense(), remembered=STAFF, default=PETTY_CASH) == (STAFF, [])
    assert choose(expense(), default=PETTY_CASH) == (PETTY_CASH, [])


def test_the_accountants_choice_is_never_chosen_again() -> None:
    assert choose(expense(CARD), STAFF, PETTY_CASH, "paid_through") == (CARD, [])


def test_a_choice_by_the_checks_is_made_again_when_the_default_changes() -> None:
    assert choose(expense(PETTY_CASH), default=CARD) == (CARD, [])


def test_no_account_to_choose_from_is_an_error_to_fix_before_approving() -> None:
    assert choose(expense()) == (None, ["paid_through_missing"])


def test_an_account_that_cannot_pay_is_passed_over_or_flagged() -> None:
    """A remembered or default account no longer a payment account is not used; the
    accountant's own choice that no longer is one is an error, never replaced."""
    assert choose(expense(), remembered=OFFICE, default=PETTY_CASH) == (PETTY_CASH, [])
    assert choose(expense(), remembered="gone", default="gone") == (None, ["paid_through_missing"])
    assert choose(expense(OFFICE), None, PETTY_CASH, "paid_through") == (
        OFFICE, ["paid_through_invalid"],
    )  # fmt: skip


def test_a_bill_is_not_paid_through_anything() -> None:
    assert choose(expense(kind=DocumentType.BILL), default=PETTY_CASH) == (None, [])
