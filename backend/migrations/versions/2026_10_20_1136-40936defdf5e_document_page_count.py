"""document page count

Revision ID: 40936defdf5e
Revises: 659dd8184f62
Create Date: 2026-10-20 11:36:48.988367+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "40936defdf5e"
down_revision: str | Sequence[str] | None = "659dd8184f62"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("page_count", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("documents", "page_count")
