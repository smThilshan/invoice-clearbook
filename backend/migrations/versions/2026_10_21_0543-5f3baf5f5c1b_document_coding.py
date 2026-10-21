"""document coding

Revision ID: 5f3baf5f5c1b
Revises: adeae8dec800
Create Date: 2026-10-21 05:43:21.350013+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "5f3baf5f5c1b"
down_revision: str | Sequence[str] | None = "adeae8dec800"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "documents", sa.Column("coding", postgresql.JSONB(astext_type=sa.Text()), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("documents", "coding")
