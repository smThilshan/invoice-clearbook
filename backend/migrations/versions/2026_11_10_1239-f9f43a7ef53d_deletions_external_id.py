"""deletions external id: a deleted company's posted documents keep their ID (decision 91)

The bill stays in the accounting system; the proof row says where it came from. Rows written
before are documents deleted one by one, which never have one (decision 45).

Revision ID: f9f43a7ef53d
Revises: e2b21f15619a
Create Date: 2026-11-10 12:39:34.787790+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f9f43a7ef53d"
down_revision: str | Sequence[str] | None = "e2b21f15619a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("deletions", sa.Column("external_id", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("deletions", "external_id")
