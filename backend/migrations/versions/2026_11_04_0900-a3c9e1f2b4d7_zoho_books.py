"""zoho books: a third accounting provider, and the data centre of a connection (decision 74)

Downgrading fails while any row still names ZOHO: remove those clients' connections first.

Revision ID: a3c9e1f2b4d7
Revises: 76c15ffdfa7b
Create Date: 2026-11-04 09:00:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a3c9e1f2b4d7"
down_revision: str | Sequence[str] | None = "76c15ffdfa7b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (table, column) of every CHECK that lists the providers
PROVIDER_COLUMNS = (
    ("clients", "accounting_provider"),
    ("accounting_connections", "provider"),
    ("ledger_accounts", "provider"),
    ("tax_rates", "provider"),
    ("contacts", "provider"),
)


def _providers(table: str, column: str, values: str) -> None:
    name = op.f(f"ck_{table}_{column}")
    op.drop_constraint(name, table, type_="check")
    op.create_check_constraint(name, table, f"{column} IN ({values})")


def upgrade() -> None:
    op.add_column("accounting_connections", sa.Column("region", sa.String(length=16)))
    for table, column in PROVIDER_COLUMNS:
        _providers(table, column, "'MANUAL', 'XERO', 'ZOHO'")


def downgrade() -> None:
    for table, column in PROVIDER_COLUMNS:
        _providers(table, column, "'MANUAL', 'XERO'")
    op.drop_column("accounting_connections", "region")
