"""sharepoint: Missan's read-only SharePoint sign-in, and runs fetched from it (decision 83)

sharepoint_connections has no client_id, like users: it is a staff member's own access, used
for every client. Runs gain where their SharePoint folder is and how far fetching it got.

Revision ID: e98d1755264b
Revises: b7d2e4f1a9c3
Create Date: 2026-11-05 09:14:45.723591+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e98d1755264b"
down_revision: str | Sequence[str] | None = "b7d2e4f1a9c3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sharepoint_connections",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("account_name", sa.String(length=255), nullable=False),
        sa.Column("account_email", sa.String(length=254), nullable=False),
        sa.Column("access_token", sa.Text(), nullable=False),
        sa.Column("refresh_token", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("connected_by", sa.String(length=128), nullable=False),
        sa.Column("needs_reconnect", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("reconnect_reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sharepoint_connections")),
    )
    op.add_column(
        "runs", sa.Column("remote", postgresql.JSONB(astext_type=sa.Text()), nullable=True)
    )
    op.add_column("runs", sa.Column("fetch_total", sa.Integer(), nullable=True))
    op.add_column("runs", sa.Column("fetched", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("runs", "fetched")
    op.drop_column("runs", "fetch_total")
    op.drop_column("runs", "remote")
    op.drop_table("sharepoint_connections")
