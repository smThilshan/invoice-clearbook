"""Manual adapter: no accounting-system connection. Master data comes in as CSV files."""

from app.adapters.accounting.manual.csv_master_data import (
    ACCOUNTS_FILE,
    CONTACTS_FILE,
    TAX_RATES_FILE,
    ManualAccountingSystem,
)

__all__ = ["ACCOUNTS_FILE", "CONTACTS_FILE", "TAX_RATES_FILE", "ManualAccountingSystem"]
