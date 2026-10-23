"""runs can be stopped (task 4.19): STOPPED joins the allowed run statuses

Revision ID: 3d9a7b5e2c18
Revises: 8c2f4a1d6e57
Create Date: 2026-10-23 12:00:00.000000+00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "3d9a7b5e2c18"
down_revision: str | Sequence[str] | None = "8c2f4a1d6e57"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(op.f("ck_runs_status"), "runs", type_="check")
    op.create_check_constraint(
        op.f("ck_runs_status"), "runs", "status IN ('RUNNING', 'COMPLETED', 'FAILED', 'STOPPED')"
    )


def downgrade() -> None:
    op.execute("UPDATE runs SET status = 'RUNNING' WHERE status = 'STOPPED'")
    op.drop_constraint(op.f("ck_runs_status"), "runs", type_="check")
    op.create_check_constraint(
        op.f("ck_runs_status"), "runs", "status IN ('RUNNING', 'COMPLETED', 'FAILED')"
    )
