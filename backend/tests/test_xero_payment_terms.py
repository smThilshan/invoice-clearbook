"""Due dates from Xero bill payment terms (decision 40)."""

from datetime import date

import pytest

from app.adapters.accounting.xero.payment_terms import due_date_from_terms


@pytest.mark.parametrize(
    ("bill_date", "day", "kind", "due"),
    [
        (date(2026, 5, 4), 30, "DAYSAFTERBILLDATE", date(2026, 6, 3)),
        (date(2026, 5, 4), 0, "DAYSAFTERBILLDATE", date(2026, 5, 4)),
        (date(2026, 5, 4), 10, "DAYSAFTERBILLMONTH", date(2026, 6, 10)),
        (date(2026, 5, 4), 20, "OFCURRENTMONTH", date(2026, 5, 20)),
        (date(2026, 2, 4), 31, "OFCURRENTMONTH", date(2026, 2, 28)),  # past the month's end
        (date(2026, 12, 9), 15, "OFFOLLOWINGMONTH", date(2027, 1, 15)),
    ],
)
def test_each_kind_of_xero_term(bill_date: date, day: int, kind: str, due: date) -> None:
    assert due_date_from_terms(bill_date, {"Day": day, "Type": kind}) == due


@pytest.mark.parametrize(
    "terms", [None, {}, {"Day": 30}, {"Day": "30", "Type": "DAYSAFTERBILLDATE"},
              {"Day": 5, "Type": "SOMETHINGNEW"}, {"Day": 0, "Type": "OFCURRENTMONTH"}],
)  # fmt: skip
def test_missing_or_unknown_terms_give_no_date(terms: dict[str, object] | None) -> None:
    assert due_date_from_terms(date(2026, 5, 4), terms) is None
