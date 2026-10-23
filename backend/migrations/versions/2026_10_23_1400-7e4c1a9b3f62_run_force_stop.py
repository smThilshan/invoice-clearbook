"""force stop (task 4.20): a stopped run can ask to abandon the model read under way

Revision ID: 7e4c1a9b3f62
Revises: 3d9a7b5e2c18
Create Date: 2026-10-23 14:00:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "7e4c1a9b3f62"
down_revision: str | Sequence[str] | None = "3d9a7b5e2c18"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "runs", sa.Column("force_stop", sa.Boolean(), server_default="false", nullable=False)
    )


def downgrade() -> None:
    op.drop_column("runs", "force_stop")
