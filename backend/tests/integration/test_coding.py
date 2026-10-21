"""Coding against the fictional F&B sample chart of accounts (infra/samples/master_data)."""

import json
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy.orm import Session

from app.adapters.accounting.manual import ManualAccountingSystem
from app.adapters.llm import ExtractionError, ExtractionRequest, ExtractionResult, ModelStatus
from app.domain.enums import AccountingProvider, DocumentType, TaxType
from app.domain.invoice import Invoice, Line
from app.models import VendorMapping
from app.pipeline.coding import (
    ChartOfAccounts,
    CodingResult,
    Confidence,
    Source,
    code_document,
    load_chart,
)
from app.services.master_data import import_master_data
from tests.integration.factories import CLIENT
from tests.test_manual_master_data import SAMPLES

pytestmark = pytest.mark.integration

VENDOR = "TRN:100999000000001"


class FakeCoder:
    model = "fake"

    def __init__(self, answer: object = None, *, fail: bool = False) -> None:
        self.answer, self.fail = answer, fail
        self.requests: list[ExtractionRequest] = []

    def extract(self, request: ExtractionRequest) -> ExtractionResult:
        self.requests.append(request)
        if self.fail:
            raise ExtractionError("model timed out after 600s")
        return ExtractionResult("fake", json.dumps(self.answer), False, 1, 1, 1)

    def check(self) -> ModelStatus:
        return ModelStatus.OK

    def close(self) -> None:
        pass


@pytest.fixture
def chart(session: Session) -> ChartOfAccounts:
    samples = ManualAccountingSystem.from_directory(SAMPLES)
    import_master_data(session, client_id=CLIENT, source=samples)
    return load_chart(session, client_id=CLIENT, provider=AccountingProvider.MANUAL)


def receipt(*descriptions: str, **fields: Any) -> Invoice:
    lines = [Line(description=d, amount=Decimal("10.00")) for d in descriptions]
    values: dict[str, Any] = {"document_type": DocumentType.RECEIPT, "currency": "AED",
                              "vendor_name": "Al Waha Hypermarket LLC",
                              "vendor_trn": "100999000000001", "vat": Decimal("1.00")}  # fmt: skip
    values.update(fields)
    return Invoice(lines=lines, **values)


def code(session: Session, chart: ChartOfAccounts, inv: Invoice, coder: FakeCoder) -> CodingResult:
    return code_document(session, inv, chart, coder, client_id=CLIENT, vendor_key=VENDOR,
                         min_consistent_approvals=2)  # fmt: skip


def learn(session: Session, account: str, approvals: int) -> None:
    mapping = VendorMapping(
        client_id=CLIENT,
        vendor_key=VENDOR,
        account_code=account,
        tax_type=TaxType.STANDARD_5,
        consistent_approvals=approvals,
    )
    session.add(mapping)
    session.flush()


def test_chart_has_active_accounts_and_mapped_tax_codes(chart: ChartOfAccounts) -> None:
    assert chart.accounts["5000"].name == "Food Cost"
    assert "6990" not in chart.accounts  # archived in the samples
    assert chart.tax_codes[TaxType.STANDARD_5] == "STD5"
    assert chart.tax_codes[TaxType.REVERSE_CHARGE] == "RCM5"
    assert all(a.code[0] in "156" for a in chart.purchase_accounts())  # assets + expenses


def test_supplier_history_wins_once_learned(session: Session, chart: ChartOfAccounts) -> None:
    learn(session, "5000", approvals=2)
    coder = FakeCoder()

    result = code(session, chart, receipt("Rice 5kg", "Cooking oil"), coder)

    assert coder.requests == []  # no model call needed
    assert [c.account_code for c in result.lines] == ["5000", "5000"]
    first = result.lines[0]
    assert (first.account_source, first.account_confidence) == (Source.SUPPLIER_HISTORY,
                                                                Confidence.HIGH)  # fmt: skip
    assert (first.tax_type, first.tax_code) == (TaxType.STANDARD_5, "STD5")


def test_too_little_history_asks_the_model(session: Session, chart: ChartOfAccounts) -> None:
    learn(session, "5000", approvals=1)
    coder = FakeCoder({"accounts": ["5000", "5040"]})

    result = code(session, chart, receipt("Rice 5kg", "Dish soap"), coder)

    assert [c.account_code for c in result.lines] == ["5000", "5040"]  # mixed receipt
    assert result.lines[1].account_source is Source.MODEL
    assert result.lines[1].account_confidence is Confidence.MEDIUM


def test_model_is_text_only_and_limited_to_purchase_accounts(
    session: Session, chart: ChartOfAccounts
) -> None:
    coder = FakeCoder({"accounts": ["5000"]})

    code(session, chart, receipt("Rice 5kg"), coder)

    [request] = coder.requests
    allowed = request.json_schema["properties"]["accounts"]["items"]["enum"]
    assert list(request.images) == []
    assert "5000" in allowed and "4000" not in allowed  # no revenue accounts for a purchase
    assert "Food Cost" in request.prompt and "Rice 5kg" in request.prompt


def test_answers_outside_the_chart_or_missing_become_none(
    session: Session, chart: ChartOfAccounts
) -> None:
    result = code(session, chart, receipt("a", "b", "c"), FakeCoder({"accounts": ["9999", "5000"]}))

    assert [c.account_code for c in result.lines] == [None, "5000", None]
    assert result.lines[0].account_source is Source.NONE


def test_model_down_leaves_accounts_for_review(session: Session, chart: ChartOfAccounts) -> None:
    result = code(session, chart, receipt("Rice"), FakeCoder(fail=True))

    assert result.lines[0].account_code is None
    assert [issue.code for issue in result.issues] == ["coding_unavailable"]


def test_archived_learned_account_falls_back_to_the_model(
    session: Session, chart: ChartOfAccounts
) -> None:
    learn(session, "6990", approvals=5)  # archived in the samples, so not in the chart

    result = code(session, chart, receipt("Rice"), FakeCoder({"accounts": ["5000"]}))

    assert result.lines[0].account_source is Source.MODEL


def test_tax_type_without_a_mapped_rate_is_warned(session: Session, chart: ChartOfAccounts) -> None:
    unmapped = ChartOfAccounts(chart.accounts, {})

    result = code(session, unmapped, receipt("Rice"), FakeCoder({"accounts": ["5000"]}))

    assert result.lines[0].tax_code is None
    assert "tax_code_unmapped" in {issue.code for issue in result.issues}


def test_non_postables_are_not_coded(session: Session, chart: ChartOfAccounts) -> None:
    quote = receipt("Rice", document_type=DocumentType.NON_POSTABLE)

    result = code(session, chart, quote, FakeCoder())

    assert result == CodingResult([], [])
    assert result.apply(quote) == quote  # its lines stay as extracted, uncoded
