"""one correction per document field

Revision ID: c649c43fab01
Revises: 37bf33157a14
Create Date: 2026-10-21 13:57:03.027469+00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "c649c43fab01"
down_revision: str | Sequence[str] | None = "37bf33157a14"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index(op.f("ix_corrections_document_id"), table_name="corrections")
    op.create_unique_constraint(
        op.f("uq_corrections_document_id_field"), "corrections", ["document_id", "field"]
    )


def downgrade() -> None:
    op.drop_constraint(op.f("uq_corrections_document_id_field"), "corrections", type_="unique")
    op.create_index(
        op.f("ix_corrections_document_id"), "corrections", ["document_id"], unique=False
    )
