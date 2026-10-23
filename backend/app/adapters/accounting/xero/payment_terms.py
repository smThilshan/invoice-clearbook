"""Due dates from Xero bill payment terms (decision 40): what Xero itself fills in when someone
types a bill without a due date. Used only when the document prints none.

Xero's term types (`PaymentTerms.Bills`: Day + Type):
    DAYSAFTERBILLDATE   Day days after the bill date
    DAYSAFTERBILLMONTH  Day days after the end of the bill's month
    OFCURRENTMONTH      the Day-th of the bill's month
    OFFOLLOWINGMONTH    the Day-th of the month after
A Day past the month's end means its last day.
"""

import calendar
from datetime import date, timedelta
from typing import Any


def due_date_from_terms(bill_date: date, terms: dict[str, Any] | None) -> date | None:
    """None when the terms are missing or not understood (then the accountant decides)."""
    if not terms:
        return None
    day, kind = terms.get("Day"), terms.get("Type")
    if not isinstance(day, int) or day < 0:
        return None
    match kind:
        case "DAYSAFTERBILLDATE":
            return bill_date + timedelta(days=day)
        case "DAYSAFTERBILLMONTH":
            return _month_end(bill_date.year, bill_date.month) + timedelta(days=day)
        case "OFCURRENTMONTH":
            return _day_of(bill_date.year, bill_date.month, day)
        case "OFFOLLOWINGMONTH":
            year, month = (bill_date.year + 1, 1) if bill_date.month == 12 else (
                bill_date.year, bill_date.month + 1)  # fmt: skip
            return _day_of(year, month, day)
    return None


def _month_end(year: int, month: int) -> date:
    return date(year, month, calendar.monthrange(year, month)[1])


def _day_of(year: int, month: int, day: int) -> date | None:
    if day < 1:
        return None
    return date(year, month, min(day, calendar.monthrange(year, month)[1]))
