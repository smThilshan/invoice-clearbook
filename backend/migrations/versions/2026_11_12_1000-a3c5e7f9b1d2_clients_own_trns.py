"""clients own trns: the business's own TRNs, to tell the customer's TRN from the supplier's
(decision 92)

Existing clients start with none: the checks then work as before until an admin enters them.

Revision ID: a3c5e7f9b1d2
Revises: f9f43a7ef53d
Create Date: 2026-11-12 10:00:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a3c5e7f9b1d2"
down_revision: str | Sequence[str] | None = "f9f43a7ef53d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "clients",
        sa.Column(
            "own_trns",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("clients", "own_trns")
