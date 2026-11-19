"""expenses are always sent as expenses (decision 101, as asked on 2026-11-19)

How expenses are sent is no longer a company setting: every company sends them as expenses
(a Zoho expense, Xero Spend Money), or as bills only while its connection cannot take them.
Bills stay bills. The column stays for that frozen choice on each outbox row's history.

Revision ID: e7a9c1d3f5b6
Revises: d6f8b0c2e4a5
Create Date: 2026-11-19 10:00:00.000000+00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "e7a9c1d3f5b6"
down_revision: str | Sequence[str] | None = "d6f8b0c2e4a5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("clients", "expenses_sent_as", server_default="EXPENSE")
    op.execute("UPDATE clients SET expenses_sent_as = 'EXPENSE'")


def downgrade() -> None:
    # Which companies sent expenses as bills before is not kept: they all go back to bills.
    op.execute("UPDATE clients SET expenses_sent_as = 'BILL'")
    op.alter_column("clients", "expenses_sent_as", server_default="BILL")
