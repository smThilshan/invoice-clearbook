"""initial schema

Hand-edited after autogenerate: status columns are VARCHAR + one named CHECK each (Alembic
renders non-native Enums with a duplicate unnamed CHECK); pg_trgm extension; posting guard
trigger (decision 19).

Revision ID: f069aa13f1ab
Revises:
Create Date: 2026-10-19 11:18:09.160719+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f069aa13f1ab"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Decision 19: only APPROVED may move to POSTING, even if code bypasses the state machine.
POSTING_GUARD_FUNCTION = """
CREATE FUNCTION documents_posting_requires_approved() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.status = 'POSTING' THEN
        IF TG_OP = 'INSERT' THEN
            RAISE EXCEPTION 'document % cannot be created as POSTING', NEW.id
                USING ERRCODE = 'check_violation';
        END IF;
        IF OLD.status <> 'APPROVED' THEN
            RAISE EXCEPTION 'document % cannot move % -> POSTING: only APPROVED can be posted',
                NEW.id, OLD.status
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;
"""

POSTING_GUARD_TRIGGER = """
CREATE TRIGGER documents_posting_requires_approved
BEFORE INSERT OR UPDATE OF status ON documents
FOR EACH ROW EXECUTE FUNCTION documents_posting_requires_approved();
"""


def upgrade() -> None:
    # Trusted extension (PG13+); also created by infra/postgres/init for dev, needed for prod.
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.create_table(
        "runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("client_id", sa.String(length=64), nullable=False),
        sa.Column(
            "direction",
            sa.String(length=32),
            nullable=False,
        ),
        sa.Column("folder_date", sa.Date(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
        ),
        sa.Column("files_found", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.String(length=128), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
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
            "direction IN ('PURCHASE', 'SALES', 'UNSORTED')", name=op.f("ck_runs_direction")
        ),
        sa.CheckConstraint(
            "status IN ('RUNNING', 'COMPLETED', 'FAILED')", name=op.f("ck_runs_status")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_runs")),
    )
    op.create_index(
        op.f("ix_runs_client_id_folder_date"), "runs", ["client_id", "folder_date"], unique=False
    )
    op.create_table(
        "vendor_mappings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("client_id", sa.String(length=64), nullable=False),
        sa.Column("vendor_key", sa.String(length=255), nullable=False),
        sa.Column("account_code", sa.String(length=32), nullable=True),
        sa.Column(
            "tax_type",
            sa.String(length=32),
            nullable=True,
        ),
        sa.Column("consistent_approvals", sa.Integer(), nullable=False),
        sa.Column("last_approved_at", sa.DateTime(timezone=True), nullable=True),
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
            "tax_type IN ('STANDARD_5', 'ZERO', 'EXEMPT', 'REVERSE_CHARGE', 'NONE')",
            name=op.f("ck_vendor_mappings_tax_type"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_vendor_mappings")),
        sa.UniqueConstraint(
            "client_id", "vendor_key", name=op.f("uq_vendor_mappings_client_id_vendor_key")
        ),
    )
    op.create_table(
        "xero_accounts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("client_id", sa.String(length=64), nullable=False),
        sa.Column("xero_id", sa.String(length=64), nullable=False),
        sa.Column("code", sa.String(length=32), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("type", sa.String(length=32), nullable=False),
        sa.Column("account_class", sa.String(length=16), nullable=True),
        sa.Column("tax_type", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("raw", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_xero_accounts")),
        sa.UniqueConstraint(
            "client_id", "xero_id", name=op.f("uq_xero_accounts_client_id_xero_id")
        ),
    )
    op.create_index(
        op.f("ix_xero_accounts_client_id_code"),
        "xero_accounts",
        ["client_id", "code"],
        unique=False,
    )
    op.create_table(
        "xero_contacts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("client_id", sa.String(length=64), nullable=False),
        sa.Column("xero_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("tax_number", sa.String(length=32), nullable=True),
        sa.Column("is_supplier", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("raw", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_xero_contacts")),
        sa.UniqueConstraint(
            "client_id", "xero_id", name=op.f("uq_xero_contacts_client_id_xero_id")
        ),
    )
    op.create_index(
        op.f("ix_xero_contacts_client_id_tax_number"),
        "xero_contacts",
        ["client_id", "tax_number"],
        unique=False,
    )
    op.create_index(
        "ix_xero_contacts_name_trgm",
        "xero_contacts",
        ["name"],
        unique=False,
        postgresql_using="gin",
        postgresql_ops={"name": "gin_trgm_ops"},
    )
    op.create_table(
        "xero_tax_rates",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("client_id", sa.String(length=64), nullable=False),
        sa.Column("tax_type", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("rate", sa.Numeric(precision=9, scale=4), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("raw", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_xero_tax_rates")),
        sa.UniqueConstraint(
            "client_id", "tax_type", name=op.f("uq_xero_tax_rates_client_id_tax_type")
        ),
    )
    op.create_table(
        "documents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("client_id", sa.String(length=64), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column(
            "direction",
            sa.String(length=32),
            nullable=False,
        ),
        sa.Column("folder_date", sa.Date(), nullable=False),
        sa.Column("source_path", sa.Text(), nullable=False),
        sa.Column("storage_key", sa.Text(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("mime_type", sa.String(length=100), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
        ),
        sa.Column(
            "document_type",
            sa.String(length=32),
            nullable=True,
        ),
        sa.Column(
            "queue",
            sa.String(length=32),
            nullable=True,
        ),
        sa.Column("invoice", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("issues", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("vendor_key", sa.String(length=255), nullable=True),
        sa.Column("business_key", sa.String(length=255), nullable=True),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column(
            "external_id",
            sa.String(length=64),
            nullable=True,
            comment="ID of the posted document in the accounting system",
        ),
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
            "direction IN ('PURCHASE', 'SALES', 'UNSORTED')", name=op.f("ck_documents_direction")
        ),
        sa.CheckConstraint(
            "document_type IN ('BILL', 'CREDIT_NOTE', 'DEBIT_NOTE', 'RECEIPT', 'SALES_INVOICE', 'NON_POSTABLE')",
            name=op.f("ck_documents_document_type"),
        ),
        sa.CheckConstraint(
            "queue IN ('READY', 'CHECK', 'EXCEPTION')", name=op.f("ck_documents_queue")
        ),
        sa.CheckConstraint(
            "status IN ('CAPTURED', 'READ', 'EXTRACTED', 'VERIFIED', 'IN_REVIEW', 'APPROVED', 'POSTING', 'POSTED', 'SYNCED', 'REJECTED', 'POST_ERROR', 'VOIDED', 'DUPLICATE', 'NON_POSTABLE', 'FAILED')",
            name=op.f("ck_documents_status"),
        ),
        sa.ForeignKeyConstraint(
            ["run_id"], ["runs.id"], name=op.f("fk_documents_run_id_runs"), ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_documents")),
    )
    op.create_index(
        op.f("ix_documents_client_id_status"), "documents", ["client_id", "status"], unique=False
    )
    op.create_index(op.f("ix_documents_run_id"), "documents", ["run_id"], unique=False)
    op.create_index(
        "uq_documents_business_key",
        "documents",
        ["client_id", "vendor_key", "document_type", "business_key"],
        unique=True,
        postgresql_where=sa.text(
            "business_key IS NOT NULL AND status NOT IN ('REJECTED', 'DUPLICATE', 'FAILED', 'VOIDED')"
        ),
    )
    op.create_index(
        "uq_documents_client_id_sha256",
        "documents",
        ["client_id", "sha256"],
        unique=True,
        postgresql_where=sa.text("status <> 'DUPLICATE'"),
    )
    op.create_table(
        "audit_log",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("client_id", sa.String(length=64), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column(
            "from_status",
            sa.String(length=32),
            nullable=False,
        ),
        sa.Column(
            "to_status",
            sa.String(length=32),
            nullable=False,
        ),
        sa.Column("actor", sa.String(length=128), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "from_status IN ('CAPTURED', 'READ', 'EXTRACTED', 'VERIFIED', 'IN_REVIEW', 'APPROVED', 'POSTING', 'POSTED', 'SYNCED', 'REJECTED', 'POST_ERROR', 'VOIDED', 'DUPLICATE', 'NON_POSTABLE', 'FAILED')",
            name=op.f("ck_audit_log_from_status"),
        ),
        sa.CheckConstraint(
            "to_status IN ('CAPTURED', 'READ', 'EXTRACTED', 'VERIFIED', 'IN_REVIEW', 'APPROVED', 'POSTING', 'POSTED', 'SYNCED', 'REJECTED', 'POST_ERROR', 'VOIDED', 'DUPLICATE', 'NON_POSTABLE', 'FAILED')",
            name=op.f("ck_audit_log_to_status"),
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name=op.f("fk_audit_log_document_id_documents"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_log")),
    )
    op.create_index(
        op.f("ix_audit_log_document_id_created_at"),
        "audit_log",
        ["document_id", "created_at"],
        unique=False,
    )
    op.create_table(
        "extractions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("client_id", sa.String(length=64), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("prompt_version", sa.String(length=32), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("raw_output", sa.Text(), nullable=False),
        sa.Column("data", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name=op.f("fk_extractions_document_id_documents"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_extractions")),
        sa.UniqueConstraint(
            "document_id", "attempt", name=op.f("uq_extractions_document_id_attempt")
        ),
    )
    op.create_table(
        "outbox",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("client_id", sa.String(length=64), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
        ),
        sa.Column("step", sa.String(length=32), nullable=True),
        sa.Column("progress", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column(
            "next_attempt_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("last_error", sa.Text(), nullable=True),
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
            "status IN ('PENDING', 'IN_PROGRESS', 'DONE', 'FAILED', 'DEAD')",
            name=op.f("ck_outbox_status"),
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name=op.f("fk_outbox_document_id_documents"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_outbox")),
    )
    op.create_index(
        op.f("ix_outbox_status_next_attempt_at"),
        "outbox",
        ["status", "next_attempt_at"],
        unique=False,
    )
    op.create_index(
        "uq_outbox_active_document_id",
        "outbox",
        ["document_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('PENDING', 'IN_PROGRESS')"),
    )
    op.create_table(
        "corrections",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("client_id", sa.String(length=64), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("extraction_id", sa.Uuid(), nullable=True),
        sa.Column("field", sa.String(length=128), nullable=False),
        sa.Column("model_value", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("final_value", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("vendor_key", sa.String(length=255), nullable=True),
        sa.Column("model", sa.String(length=128), nullable=True),
        sa.Column("prompt_version", sa.String(length=32), nullable=True),
        sa.Column("corrected_by", sa.String(length=128), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name=op.f("fk_corrections_document_id_documents"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["extraction_id"],
            ["extractions.id"],
            name=op.f("fk_corrections_extraction_id_extractions"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_corrections")),
    )
    op.create_index(
        op.f("ix_corrections_client_id_vendor_key"),
        "corrections",
        ["client_id", "vendor_key"],
        unique=False,
    )
    op.create_index(
        op.f("ix_corrections_document_id"), "corrections", ["document_id"], unique=False
    )
    op.execute(POSTING_GUARD_FUNCTION)
    op.execute(POSTING_GUARD_TRIGGER)


def downgrade() -> None:
    op.execute("DROP TRIGGER documents_posting_requires_approved ON documents")
    op.execute("DROP FUNCTION documents_posting_requires_approved()")
    op.drop_index(op.f("ix_corrections_document_id"), table_name="corrections")
    op.drop_index(op.f("ix_corrections_client_id_vendor_key"), table_name="corrections")
    op.drop_table("corrections")
    op.drop_index(
        "uq_outbox_active_document_id",
        table_name="outbox",
        postgresql_where=sa.text("status IN ('PENDING', 'IN_PROGRESS')"),
    )
    op.drop_index(op.f("ix_outbox_status_next_attempt_at"), table_name="outbox")
    op.drop_table("outbox")
    op.drop_table("extractions")
    op.drop_index(op.f("ix_audit_log_document_id_created_at"), table_name="audit_log")
    op.drop_table("audit_log")
    op.drop_index(
        "uq_documents_client_id_sha256",
        table_name="documents",
        postgresql_where=sa.text("status <> 'DUPLICATE'"),
    )
    op.drop_index(
        "uq_documents_business_key",
        table_name="documents",
        postgresql_where=sa.text(
            "business_key IS NOT NULL AND status NOT IN ('REJECTED', 'DUPLICATE', 'FAILED', 'VOIDED')"
        ),
    )
    op.drop_index(op.f("ix_documents_run_id"), table_name="documents")
    op.drop_index(op.f("ix_documents_client_id_status"), table_name="documents")
    op.drop_table("documents")
    op.drop_table("xero_tax_rates")
    op.drop_index(
        "ix_xero_contacts_name_trgm",
        table_name="xero_contacts",
        postgresql_using="gin",
        postgresql_ops={"name": "gin_trgm_ops"},
    )
    op.drop_index(op.f("ix_xero_contacts_client_id_tax_number"), table_name="xero_contacts")
    op.drop_table("xero_contacts")
    op.drop_index(op.f("ix_xero_accounts_client_id_code"), table_name="xero_accounts")
    op.drop_table("xero_accounts")
    op.drop_table("vendor_mappings")
    op.drop_index(op.f("ix_runs_client_id_folder_date"), table_name="runs")
    op.drop_table("runs")
