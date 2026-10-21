"""document verified fields

Revision ID: 37bf33157a14
Revises: 5099a285da11
Create Date: 2026-10-21 07:19:26.415825+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "37bf33157a14"
down_revision: str | Sequence[str] | None = "5099a285da11"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column(
            "verified_fields",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("documents", "verified_fields")
