from decimal import Decimal
from typing import Any

import pytest

from app.domain.enums import AccountClass, AmountType, DocumentType, TaxType
from app.domain.invoice import Invoice, Line
from app.pipeline.coding import (
    Account,
    ChartOfAccounts,
    CodingResult,
    Confidence,
    LineCoding,
    Source,
    check_codes,
    decide_tax_type,
)

TRN = "100999000000001"
CHART = ChartOfAccounts(
    accounts={
        "5000": Account("5000", "Food Cost", AccountClass.EXPENSE),
        "1500": Account("1500", "Kitchen Equipment", AccountClass.ASSET),
        "4000": Account("4000", "Food Sales", AccountClass.REVENUE),
    },
    tax_codes={TaxType.STANDARD_5: "STD5", TaxType.ZERO: "ZERO"},
)


def invoice(**fields: Any) -> Invoice:
    values: dict[str, Any] = {"document_type": DocumentType.BILL, "currency": "AED"}
    values.update(fields)
    return Invoice(**values)


# --- tax type: UAE VAT rules (architecture §9) --------------------------------------------


@pytest.mark.parametrize(
    ("fields", "expected"),
    [
        ({"vat": Decimal("50"), "vendor_trn": TRN}, (TaxType.STANDARD_5, Confidence.HIGH)),
        ({"vat": Decimal("-50"), "vendor_trn": TRN}, (TaxType.STANDARD_5, Confidence.HIGH)),
        ({"vat": Decimal("0"), "vendor_trn": TRN}, (TaxType.ZERO, Confidence.MEDIUM)),
        ({"amount_type": AmountType.NO_TAX, "vendor_trn": TRN}, (TaxType.ZERO, Confidence.MEDIUM)),
        ({"vat": Decimal("0"), "currency": "USD"}, (TaxType.REVERSE_CHARGE, Confidence.MEDIUM)),
        ({"currency": "EUR", "vendor_trn": None}, (TaxType.REVERSE_CHARGE, Confidence.MEDIUM)),
        ({"vat": None, "vendor_trn": None}, (TaxType.NONE, Confidence.MEDIUM)),
        ({"vat": None, "vendor_trn": TRN}, (None, Confidence.NONE)),  # VAT just not read
        ({"vat": Decimal("0"), "vendor_trn": "12345"}, (TaxType.NONE, Confidence.MEDIUM)),
    ],
)
def test_tax_type_from_vat_rules(fields: dict[str, Any], expected: tuple[Any, Any]) -> None:
    assert decide_tax_type(invoice(**fields)) == expected


# --- codes rule (architecture §8) ---------------------------------------------------------


def line(account: str | None, tax: TaxType | None = TaxType.STANDARD_5) -> Line:
    return Line(description="x", amount=Decimal("10"), account_code=account, tax_type=tax)


def test_active_expense_and_asset_accounts_pass() -> None:
    assert check_codes(invoice(lines=[line("5000"), line("1500")]), CHART) == []


@pytest.mark.parametrize(
    ("bad_line", "code", "field"),
    [
        (line(None), "account_missing", "lines[0].account_code"),
        (line("9999"), "account_unknown", "lines[0].account_code"),
        (line("4000"), "account_wrong_class", "lines[0].account_code"),
        (line("5000", None), "tax_missing", "lines[0].tax_type"),
        (line("5000", TaxType.EXEMPT), "tax_unmapped", "lines[0].tax_type"),
    ],
)
def test_bad_codes_are_errors(bad_line: Line, code: str, field: str) -> None:
    [issue] = check_codes(invoice(lines=[bad_line]), CHART)

    assert (issue.code, issue.field, issue.severity.value) == (code, field, "ERROR")


def test_non_postables_need_no_codes() -> None:
    assert check_codes(invoice(document_type=DocumentType.NON_POSTABLE, lines=[line(None)]),
                       CHART) == []  # fmt: skip


def test_apply_puts_suggestions_on_lines_without_touching_extracted_values() -> None:
    original = invoice(lines=[Line(description="Rice 5kg", amount=Decimal("90.00"))])
    coding = CodingResult(
        [LineCoding(0, "5000", Source.MODEL, Confidence.MEDIUM, TaxType.STANDARD_5, "STD5",
                    Confidence.HIGH)],
        [],
    )  # fmt: skip

    coded = coding.apply(original)

    assert (coded.lines[0].account_code, coded.lines[0].tax_type) == ("5000", TaxType.STANDARD_5)
    assert coded.lines[0].amount == Decimal("90.00")
    assert original.lines[0].account_code is None  # a new object, original untouched
