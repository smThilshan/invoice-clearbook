"""Import master data CSVs (ledger_accounts.csv, tax_rates.csv, contacts.csv) from a folder.

    make seed                                         # fictional F&B sample, default client
    make import-master-data DIR=path [CLIENT=id]      # your own files

Whichever of the three files exist are imported, all-or-nothing, and re-running is safe.
The HTTP endpoint arrives with the Workbench, behind login (task 4.2).
"""

import argparse
import sys
from pathlib import Path

from app.adapters.accounting.base import MasterDataError
from app.adapters.accounting.manual import (
    ACCOUNTS_FILE,
    CONTACTS_FILE,
    TAX_RATES_FILE,
    ManualAccountingSystem,
)
from app.core.config import get_settings
from app.core.db import session_scope
from app.services.master_data import Counts, import_master_data


def _line(label: str, counts: Counts | None) -> str:
    if counts is None:
        return f"  {label:<10} not in this import"
    return (
        f"  {label:<10} {counts.created} created, {counts.updated} updated, "
        f"{counts.unchanged} unchanged"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("directory", type=Path)
    parser.add_argument("--client", default=get_settings().default_client_id)
    args = parser.parse_args()

    files = (ACCOUNTS_FILE, TAX_RATES_FILE, CONTACTS_FILE)
    if not any((args.directory / name).is_file() for name in files):
        print(f"no master data files in {args.directory}", file=sys.stderr)
        return 1

    source = ManualAccountingSystem.from_directory(args.directory)
    try:
        with session_scope() as session:
            report = import_master_data(session, client_id=args.client, source=source)
    except MasterDataError as exc:
        print(f"import rejected, nothing was changed ({len(exc.problems)} problem(s)):")
        for problem in exc.problems:
            print(f"  - {problem}")
        return 1

    print(f"imported master data for client '{args.client}' ({source.provider}):")
    print(_line("accounts", report.accounts))
    print(_line("tax rates", report.tax_rates))
    print(_line("contacts", report.contacts))
    return 0


if __name__ == "__main__":
    sys.exit(main())
