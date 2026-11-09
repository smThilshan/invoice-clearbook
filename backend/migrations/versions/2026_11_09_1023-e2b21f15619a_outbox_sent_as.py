"""outbox sent as: each approved document is sent as a bill or an expense (decision 89)

Rows approved before were all sent as bills.

Revision ID: e2b21f15619a
Revises: ed163d1b0a9c
Create Date: 2026-11-09 10:23:03.880585+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e2b21f15619a"
down_revision: str | Sequence[str] | None = "ed163d1b0a9c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "outbox", sa.Column("sent_as", sa.String(length=32), server_default="BILL", nullable=False)
    )
    op.create_check_constraint(
        op.f("ck_outbox_outbox_sent_as"), "outbox", "sent_as IN ('BILL', 'EXPENSE')"
    )


def downgrade() -> None:
    op.drop_column("outbox", "sent_as")
