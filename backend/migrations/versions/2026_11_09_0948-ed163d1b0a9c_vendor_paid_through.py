"""vendor paid through: the account a supplier's expenses were last paid through (decision 89)

Starts empty: learned from expenses approved from now on.

Revision ID: ed163d1b0a9c
Revises: a0ad260f42c7
Create Date: 2026-11-09 09:48:39.247357+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "ed163d1b0a9c"
down_revision: str | Sequence[str] | None = "a0ad260f42c7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("vendor_mappings", sa.Column("paid_through", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("vendor_mappings", "paid_through")
