"""every company holds approved documents for Send (decision 98, as asked on 2026-11-16)

Approved documents wait in the review queue's Approved list until someone sends them, one by
one, chosen, or all. An admin may still set a company to send right after approval.

Revision ID: d6f8b0c2e4a5
Revises: c5e7a9b1d3f4
Create Date: 2026-11-16 20:00:00.000000+00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "d6f8b0c2e4a5"
down_revision: str | Sequence[str] | None = "c5e7a9b1d3f4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("UPDATE clients SET send_mode = 'ON_SEND'")


def downgrade() -> None:
    # Which companies sent straight away before is not kept: they all go back to it.
    op.execute("UPDATE clients SET send_mode = 'AFTER_APPROVAL'")
