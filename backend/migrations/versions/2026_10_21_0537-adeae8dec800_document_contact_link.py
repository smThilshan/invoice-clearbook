"""document contact link

Revision ID: adeae8dec800
Revises: 3551b7cee866
Create Date: 2026-10-21 05:37:33.825369+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "adeae8dec800"
down_revision: str | Sequence[str] | None = "3551b7cee866"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("contact_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        op.f("fk_documents_contact_id_contacts"),
        "documents",
        "contacts",
        ["contact_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("fk_documents_contact_id_contacts"), "documents", type_="foreignkey")
    op.drop_column("documents", "contact_id")
