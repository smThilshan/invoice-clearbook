"""line grouping: By item is the default, for every client (decision 75)

Bills approved before keep how they were frozen on their outbox row.

Revision ID: b7d2e4f1a9c3
Revises: a3c9e1f2b4d7
Create Date: 2026-11-04 14:00:00.000000+00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "b7d2e4f1a9c3"
down_revision: str | Sequence[str] | None = "a3c9e1f2b4d7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for table in ("clients", "outbox"):
        op.alter_column(table, "line_grouping", server_default="BY_ITEM")
    op.execute("UPDATE clients SET line_grouping = 'BY_ITEM'")


def downgrade() -> None:
    for table in ("clients", "outbox"):
        op.alter_column(table, "line_grouping", server_default="BY_ACCOUNT")
