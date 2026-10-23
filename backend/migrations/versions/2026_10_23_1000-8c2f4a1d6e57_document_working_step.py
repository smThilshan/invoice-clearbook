"""document working step: which pipeline step runs now, and since when (task 4.14)

Revision ID: 8c2f4a1d6e57
Revises: 5b1d2e7c9a40
Create Date: 2026-10-23 10:00:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "8c2f4a1d6e57"
down_revision: str | Sequence[str] | None = "5b1d2e7c9a40"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("working_step", sa.String(length=16), nullable=True))
    op.add_column(
        "documents", sa.Column("working_since", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("documents", "working_since")
    op.drop_column("documents", "working_step")
