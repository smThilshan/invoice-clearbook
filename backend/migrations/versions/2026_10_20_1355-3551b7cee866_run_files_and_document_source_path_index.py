"""run files and document source path index

Revision ID: 3551b7cee866
Revises: 40936defdf5e
Create Date: 2026-10-20 13:55:12.558314+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "3551b7cee866"
down_revision: str | Sequence[str] | None = "40936defdf5e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        op.f("ix_documents_client_id_source_path"),
        "documents",
        ["client_id", "source_path"],
        unique=False,
    )
    op.add_column(
        "runs", sa.Column("files", postgresql.JSONB(astext_type=sa.Text()), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("runs", "files")
    op.drop_index(op.f("ix_documents_client_id_source_path"), table_name="documents")
