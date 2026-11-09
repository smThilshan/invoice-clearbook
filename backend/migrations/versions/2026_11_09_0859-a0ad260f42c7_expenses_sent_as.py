"""expenses sent as: bill (as before) or expense, and the account usually paid through (decision 89)

Every client starts on BILL: nothing is sent differently until an admin switches a client.

Revision ID: a0ad260f42c7
Revises: 80b4bb3a9a3e
Create Date: 2026-11-09 08:59:39.395317+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a0ad260f42c7"
down_revision: str | Sequence[str] | None = "80b4bb3a9a3e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "clients",
        sa.Column("expenses_sent_as", sa.String(length=32), server_default="BILL", nullable=False),
    )
    op.create_check_constraint(
        op.f("ck_clients_expenses_sent_as"), "clients", "expenses_sent_as IN ('BILL', 'EXPENSE')"
    )
    op.add_column("clients", sa.Column("paid_through", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("clients", "paid_through")
    op.drop_column("clients", "expenses_sent_as")
