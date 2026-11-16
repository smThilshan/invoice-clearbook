"""send mode and held outbox rows: approved documents wait for Send (decision 98)

Clients from before keep sending straight away (AFTER_APPROVAL); new clients hold approved
documents until someone presses Send (ON_SEND). A held row is never claimed by the worker, and
still the document's one live row. `released_by` / `released_at`: who let it go, and when; rows
from before were released when they were approved.

Revision ID: c5e7a9b1d3f4
Revises: b4d6f8a0c2e3
Create Date: 2026-11-16 18:00:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c5e7a9b1d3f4"
down_revision: str | Sequence[str] | None = "b4d6f8a0c2e3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STATUSES = "'PENDING', 'IN_PROGRESS', 'DONE', 'FAILED', 'DEAD'"


def upgrade() -> None:
    op.add_column(
        "clients",
        sa.Column(
            "send_mode",
            sa.Enum(
                "AFTER_APPROVAL",
                "ON_SEND",
                name="send_mode",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            server_default="AFTER_APPROVAL",  # every client so far: nothing changes for them
            nullable=False,
        ),
    )
    op.alter_column("clients", "send_mode", server_default="ON_SEND")  # new clients hold

    op.drop_constraint(op.f("ck_outbox_status"), "outbox", type_="check")
    op.create_check_constraint(
        op.f("ck_outbox_status"), "outbox", f"status IN ('HELD', {STATUSES})"
    )
    op.add_column("outbox", sa.Column("released_by", sa.String(length=128), nullable=True))
    op.add_column("outbox", sa.Column("released_at", sa.DateTime(timezone=True), nullable=True))
    op.execute("UPDATE outbox SET released_at = created_at")
    op.drop_index("uq_outbox_active_document_id", table_name="outbox")
    op.create_index(
        "uq_outbox_active_document_id",
        "outbox",
        ["document_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('HELD', 'PENDING', 'IN_PROGRESS')"),
    )


def downgrade() -> None:
    op.execute("UPDATE outbox SET status = 'PENDING' WHERE status = 'HELD'")  # sent straight away
    op.drop_index("uq_outbox_active_document_id", table_name="outbox")
    op.create_index(
        "uq_outbox_active_document_id",
        "outbox",
        ["document_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('PENDING', 'IN_PROGRESS')"),
    )
    op.drop_column("outbox", "released_at")
    op.drop_column("outbox", "released_by")
    op.drop_constraint(op.f("ck_outbox_status"), "outbox", type_="check")
    op.create_check_constraint(op.f("ck_outbox_status"), "outbox", f"status IN ({STATUSES})")
    op.drop_column("clients", "send_mode")
