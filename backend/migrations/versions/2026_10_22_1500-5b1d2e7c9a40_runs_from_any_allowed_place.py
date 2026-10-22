"""runs from any allowed place: run root, optional client folder (decision 35)

Revision ID: 5b1d2e7c9a40
Revises: c649c43fab01
Create Date: 2026-10-22 15:00:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "5b1d2e7c9a40"
down_revision: str | Sequence[str] | None = "c649c43fab01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("runs", sa.Column("root_path", sa.Text(), nullable=True))
    op.alter_column("clients", "inbox_path", existing_type=sa.Text(), nullable=True)


def downgrade() -> None:
    op.alter_column("clients", "inbox_path", existing_type=sa.Text(), nullable=False)
    op.drop_column("runs", "root_path")
