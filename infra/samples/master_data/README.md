# Sample master data (fictional)

A made-up F&B business, for development and tests. **Not real data**: supplier names are invented
and TRNs use the fake `100999…` range.

Load into the default client: `make seed`. Import your own files the same way:
`make import-master-data DIR=path/to/folder [CLIENT=id]`.

| File | Columns (`*` required) |
|---|---|
| `ledger_accounts.csv` | `code*`, `name*`, `class*` (ASSET, LIABILITY, EQUITY, REVENUE, EXPENSE), `default_tax_code`, `active` |
| `tax_rates.csv` | `code*`, `name*`, `rate` (percent), `tax_type` (STANDARD_5, ZERO, EXEMPT, REVERSE_CHARGE, NONE), `active` |
| `contacts.csv` | `name*`, `tax_number`, `is_supplier`, `active` |

`active` / `is_supplier`: yes or no (blank = yes). Extra columns are kept as-is.
Covers: petty cash purchases from supermarkets, a supplier without TRN (`Corner Grocery`),
a foreign supplier for reverse charge (`Spice Route Exports`), and an archived account (`6990`).
