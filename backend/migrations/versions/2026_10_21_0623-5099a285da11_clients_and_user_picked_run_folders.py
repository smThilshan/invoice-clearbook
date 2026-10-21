"""clients and user picked run folders

Revision ID: 5099a285da11
Revises: 5f3baf5f5c1b
Create Date: 2026-10-21 06:23:08.780442+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "5099a285da11"
down_revision: str | Sequence[str] | None = "5f3baf5f5c1b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "clients",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("client_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("inbox_path", sa.Text(), nullable=False),
        sa.Column("accounting_provider", sa.String(length=32), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
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
        sa.CheckConstraint(
            "accounting_provider IN ('MANUAL', 'XERO')", name=op.f("ck_clients_accounting_provider")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_clients")),
        sa.UniqueConstraint("client_id", name=op.f("uq_clients_client_id")),
    )
    op.alter_column("documents", "folder_date", existing_type=sa.DATE(), nullable=True)
    op.add_column("runs", sa.Column("folder", sa.Text(), server_default="", nullable=False))
    op.add_column("runs", sa.Column("failure_reason", sa.Text(), nullable=True))
    op.alter_column("runs", "folder_date", existing_type=sa.DATE(), nullable=True)


def downgrade() -> None:
    op.alter_column("runs", "folder_date", existing_type=sa.DATE(), nullable=False)
    op.drop_column("runs", "failure_reason")
    op.drop_column("runs", "folder")
    op.alter_column("documents", "folder_date", existing_type=sa.DATE(), nullable=False)
    op.drop_table("clients")
