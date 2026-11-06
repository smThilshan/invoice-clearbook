"""One total line (decision 86): lines that do not add up are sent as the printed total and VAT,
only when that is exact to the fils. Made-up amounts."""

from decimal import Decimal
from typing import Any

import pytest

from app.domain.enums import AmountType, DocumentType, TaxType
from app.domain.invoice import Invoice, Line
from app.pipeline.total_line import TotalLine, total_line


def line(amount: str, **changes: Any) -> Line:
    fields: dict[str, Any] = {"description": "Item", "amount": Decimal(amount),
                              "account_code": "429", "tax_type": TaxType.STANDARD_5}  # fmt: skip
    return Line(**{**fields, **changes})


def invoice(*lines: Line, vat: str | None = "7.24", total: str | None = "152.00") -> Invoice:
    return Invoice(
        document_type=DocumentType.BILL,
        vat=Decimal(vat) if vat is not None else None,
        total=Decimal(total) if total is not None else None,
        lines=list(lines) or [line("100.00"), line("52.48")],  # 152.48: a line misread
    )


def test_lines_that_do_not_add_up_are_sent_as_the_amount_before_vat() -> None:
    """Amexhub: 152.48 read in the lines, 144.76 + 7.24 = 152.00 printed."""
    assert total_line(invoice()) == TotalLine(AmountType.EXCLUSIVE, Decimal("144.76"), "429")


def test_when_only_the_vat_in_the_total_is_exact_the_total_is_sent_vat_included() -> None:
    """5% of 9.70 rounds to 0.49, not the 0.48 printed; 10.18 / 21 rounds to 0.48: sent with VAT
    included, the accounting system gives back both printed amounts."""
    sent = total_line(invoice(line("9.00"), vat="0.48", total="10.18"))

    assert sent == TotalLine(AmountType.INCLUSIVE, Decimal("10.18"), "429")


@pytest.mark.parametrize(
    ("inv", "why"),
    [
        (invoice(line("144.76")), "the lines add up before VAT"),
        (invoice(line("152.00")), "the lines add up with VAT"),
        (invoice(vat="10.03", total="210.50"), "VAT 1 fils off 5%: Xero would send 10.02"),
        (invoice(total=None), "no total read"),
        (invoice(vat=None), "no VAT read"),
        (invoice(vat="0", total="152.00"), "no VAT: nothing for the totals to prove"),
        (invoice(line("100.00"), line("52.48", account_code="300")), "two accounts"),
        (invoice(line("100.00"), line("52.48", tax_type=TaxType.ZERO)), "a zero-rated line"),
    ],
)
def test_anything_short_of_exact_leaves_the_lines_to_the_accountant(inv: Invoice, why: str) -> None:
    assert total_line(inv) is None, why


def test_no_lines_no_total_line() -> None:
    assert total_line(Invoice(document_type=DocumentType.BILL, vat=Decimal("7.24"),
                              total=Decimal("152.00"))) is None  # fmt: skip


def test_lines_not_yet_coded_still_share_their_account() -> None:
    """Coding comes later: posting refuses a line without an account anyway."""
    inv = invoice(line("100.00", account_code=None, tax_type=None),
                  line("52.48", account_code=None, tax_type=None))  # fmt: skip

    assert total_line(inv) == TotalLine(AmountType.EXCLUSIVE, Decimal("144.76"), None)
