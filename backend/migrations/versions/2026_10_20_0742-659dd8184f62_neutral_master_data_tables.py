"""neutral master data tables

Replaces the empty, never-used xero_* tables with neutral master data tables that any
accounting adapter fills (decision 20). Downgrade restores the old tables, empty.

Revision ID: 659dd8184f62
Revises: f069aa13f1ab
Create Date: 2026-10-20 07:42:47.944309+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "659dd8184f62"
down_revision: str | Sequence[str] | None = "f069aa13f1ab"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "contacts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("client_id", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("external_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("tax_number", sa.String(length=32), nullable=True),
        sa.Column("is_supplier", sa.Boolean(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
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
        sa.CheckConstraint("provider IN ('MANUAL', 'XERO')", name=op.f("ck_contacts_provider")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_contacts")),
        sa.UniqueConstraint(
            "client_id",
            "provider",
            "external_id",
            name=op.f("uq_contacts_client_id_provider_external_id"),
        ),
    )
    op.create_index(
        op.f("ix_contacts_client_id_tax_number"),
        "contacts",
        ["client_id", "tax_number"],
        unique=False,
    )
    op.create_index(
        "ix_contacts_name_trgm",
        "contacts",
        ["name"],
        unique=False,
        postgresql_using="gin",
        postgresql_ops={"name": "gin_trgm_ops"},
    )
    op.create_table(
        "ledger_accounts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("client_id", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("external_id", sa.String(length=64), nullable=False),
        sa.Column("code", sa.String(length=32), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("account_class", sa.String(length=32), nullable=False),
        sa.Column("default_tax_code", sa.String(length=64), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
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
        sa.CheckConstraint(
            "account_class IN ('ASSET', 'LIABILITY', 'EQUITY', 'REVENUE', 'EXPENSE')",
            name=op.f("ck_ledger_accounts_account_class"),
        ),
        sa.CheckConstraint(
            "provider IN ('MANUAL', 'XERO')", name=op.f("ck_ledger_accounts_provider")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ledger_accounts")),
        sa.UniqueConstraint(
            "client_id",
            "provider",
            "external_id",
            name=op.f("uq_ledger_accounts_client_id_provider_external_id"),
        ),
    )
    op.create_index(
        "uq_ledger_accounts_code",
        "ledger_accounts",
        ["client_id", "provider", "code"],
        unique=True,
        postgresql_where=sa.text("code IS NOT NULL"),
    )
    op.create_table(
        "tax_rates",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("client_id", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("external_id", sa.String(length=64), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("rate", sa.Numeric(precision=9, scale=4), nullable=True),
        sa.Column("tax_type", sa.String(length=32), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
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
        sa.CheckConstraint("provider IN ('MANUAL', 'XERO')", name=op.f("ck_tax_rates_provider")),
        sa.CheckConstraint(
            "tax_type IN ('STANDARD_5', 'ZERO', 'EXEMPT', 'REVERSE_CHARGE', 'NONE')",
            name=op.f("ck_tax_rates_tax_type"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tax_rates")),
        sa.UniqueConstraint(
            "client_id", "provider", "code", name=op.f("uq_tax_rates_client_id_provider_code")
        ),
        sa.UniqueConstraint(
            "client_id",
            "provider",
            "external_id",
            name=op.f("uq_tax_rates_client_id_provider_external_id"),
        ),
    )
    op.drop_index(op.f("ix_xero_accounts_client_id_code"), table_name="xero_accounts")
    op.drop_table("xero_accounts")
    op.drop_index(op.f("ix_xero_contacts_client_id_tax_number"), table_name="xero_contacts")
    op.drop_index(
        op.f("ix_xero_contacts_name_trgm"),
        table_name="xero_contacts",
        postgresql_ops={"name": "gin_trgm_ops"},
        postgresql_using="gin",
    )
    op.drop_table("xero_contacts")
    op.drop_table("xero_tax_rates")


def downgrade() -> None:
    op.create_table(
        "xero_tax_rates",
        sa.Column("id", sa.UUID(), autoincrement=False, nullable=False),
        sa.Column("client_id", sa.VARCHAR(length=64), autoincrement=False, nullable=False),
        sa.Column("tax_type", sa.VARCHAR(length=64), autoincrement=False, nullable=False),
        sa.Column("name", sa.VARCHAR(length=255), autoincrement=False, nullable=False),
        sa.Column("rate", sa.NUMERIC(precision=9, scale=4), autoincrement=False, nullable=True),
        sa.Column("status", sa.VARCHAR(length=16), autoincrement=False, nullable=False),
        sa.Column(
            "raw", postgresql.JSONB(astext_type=sa.Text()), autoincrement=False, nullable=False
        ),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            autoincrement=False,
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            autoincrement=False,
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_xero_tax_rates")),
        sa.UniqueConstraint(
            "client_id",
            "tax_type",
            name=op.f("uq_xero_tax_rates_client_id_tax_type"),
            postgresql_include=[],
            postgresql_nulls_not_distinct=False,
        ),
    )
    op.create_table(
        "xero_contacts",
        sa.Column("id", sa.UUID(), autoincrement=False, nullable=False),
        sa.Column("client_id", sa.VARCHAR(length=64), autoincrement=False, nullable=False),
        sa.Column("xero_id", sa.VARCHAR(length=64), autoincrement=False, nullable=False),
        sa.Column("name", sa.VARCHAR(length=255), autoincrement=False, nullable=False),
        sa.Column("tax_number", sa.VARCHAR(length=32), autoincrement=False, nullable=True),
        sa.Column("is_supplier", sa.BOOLEAN(), autoincrement=False, nullable=False),
        sa.Column("status", sa.VARCHAR(length=16), autoincrement=False, nullable=False),
        sa.Column(
            "raw", postgresql.JSONB(astext_type=sa.Text()), autoincrement=False, nullable=False
        ),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            autoincrement=False,
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            autoincrement=False,
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_xero_contacts")),
        sa.UniqueConstraint(
            "client_id",
            "xero_id",
            name=op.f("uq_xero_contacts_client_id_xero_id"),
            postgresql_include=[],
            postgresql_nulls_not_distinct=False,
        ),
    )
    op.create_index(
        op.f("ix_xero_contacts_name_trgm"),
        "xero_contacts",
        ["name"],
        unique=False,
        postgresql_ops={"name": "gin_trgm_ops"},
        postgresql_using="gin",
    )
    op.create_index(
        op.f("ix_xero_contacts_client_id_tax_number"),
        "xero_contacts",
        ["client_id", "tax_number"],
        unique=False,
    )
    op.create_table(
        "xero_accounts",
        sa.Column("id", sa.UUID(), autoincrement=False, nullable=False),
        sa.Column("client_id", sa.VARCHAR(length=64), autoincrement=False, nullable=False),
        sa.Column("xero_id", sa.VARCHAR(length=64), autoincrement=False, nullable=False),
        sa.Column("code", sa.VARCHAR(length=32), autoincrement=False, nullable=True),
        sa.Column("name", sa.VARCHAR(length=255), autoincrement=False, nullable=False),
        sa.Column("type", sa.VARCHAR(length=32), autoincrement=False, nullable=False),
        sa.Column("account_class", sa.VARCHAR(length=16), autoincrement=False, nullable=True),
        sa.Column("tax_type", sa.VARCHAR(length=64), autoincrement=False, nullable=True),
        sa.Column("status", sa.VARCHAR(length=16), autoincrement=False, nullable=False),
        sa.Column(
            "raw", postgresql.JSONB(astext_type=sa.Text()), autoincrement=False, nullable=False
        ),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            autoincrement=False,
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            autoincrement=False,
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_xero_accounts")),
        sa.UniqueConstraint(
            "client_id",
            "xero_id",
            name=op.f("uq_xero_accounts_client_id_xero_id"),
            postgresql_include=[],
            postgresql_nulls_not_distinct=False,
        ),
    )
    op.create_index(
        op.f("ix_xero_accounts_client_id_code"),
        "xero_accounts",
        ["client_id", "code"],
        unique=False,
    )
    op.drop_table("tax_rates")
    op.drop_index(
        "uq_ledger_accounts_code",
        table_name="ledger_accounts",
        postgresql_where=sa.text("code IS NOT NULL"),
    )
    op.drop_table("ledger_accounts")
    op.drop_index(
        "ix_contacts_name_trgm",
        table_name="contacts",
        postgresql_using="gin",
        postgresql_ops={"name": "gin_trgm_ops"},
    )
    op.drop_index(op.f("ix_contacts_client_id_tax_number"), table_name="contacts")
    op.drop_table("contacts")
