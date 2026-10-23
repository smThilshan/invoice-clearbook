"""What a check reports: an issue on a field, with a severity and a reason for the accountant.

Errors block approval; warnings are shown in yellow (architecture §8). Issues flag, they never
change the data (rule 8). Messages are for the Workbench and may quote document values, so
issues are stored with the document and never logged.
"""

from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any


class Severity(StrEnum):
    ERROR = "ERROR"  # blocks approval
    WARNING = "WARNING"  # shown, does not block


@dataclass(frozen=True, slots=True)
class Issue:
    code: str  # stable identifier, e.g. "totals_mismatch" (for filters, stats, tests)
    severity: Severity
    field: str | None  # e.g. "total", "lines[2].amount"; None = whole document
    message: str
    # The value printed on the document, when a check read it itself (e.g. the supplier TRN):
    # offered to the accountant in review, never applied automatically (rule 8).
    suggestion: str | None = None
    # Charges outside the lines that explain the total (task 4.13), offered as new lines:
    # [{"label": "Delivery fee", "amount": "5.99"}]. Never applied automatically.
    charges: list[dict[str, str]] | None = None

    def to_json(self) -> dict[str, Any]:
        return asdict(self)
