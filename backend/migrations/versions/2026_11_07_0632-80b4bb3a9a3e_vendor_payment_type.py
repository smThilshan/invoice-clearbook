"""vendor payment type: bill or expense as approved for each supplier (decision 88)

Starts empty: approvals before decision 88 posted expenses as bills too (decision 50), so they
do not say how a supplier is paid.

Revision ID: 80b4bb3a9a3e
Revises: e98d1755264b
Create Date: 2026-11-07 06:32:29.964567+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "80b4bb3a9a3e"
down_revision: str | Sequence[str] | None = "e98d1755264b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("vendor_mappings", sa.Column("payment_type", sa.String(length=32), nullable=True))
    op.add_column(
        "vendor_mappings",
        sa.Column("payment_approvals", sa.Integer(), server_default="0", nullable=False),
    )
    op.create_check_constraint(
        op.f("ck_vendor_mappings_vendor_payment_type"),
        "vendor_mappings",
        "payment_type IN ('BILL', 'CREDIT_NOTE', 'DEBIT_NOTE', 'RECEIPT', 'SALES_INVOICE', "
        "'NON_POSTABLE')",
    )


def downgrade() -> None:
    op.drop_column("vendor_mappings", "payment_approvals")
    op.drop_column("vendor_mappings", "payment_type")
