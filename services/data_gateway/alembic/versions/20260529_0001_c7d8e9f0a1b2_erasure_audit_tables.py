"""erasure_audit_tables

Sprint 5 — S5-004: Add DPDP right-to-erasure tables and sessions soft-delete.

Tables created:
  - erasure_requests  (request_id UUID PK, user_id FK→users, requested_by UUID,
                       reason text nullable, status varchar(16) default 'pending',
                       scheduled_for timestamptz, completed_at nullable,
                       artifacts jsonb nullable, created_at)
  - audit_log         (event_id UUID PK, actor_id UUID nullable, actor_type varchar(16),
                       action varchar(64), resource_type varchar(32), resource_id UUID,
                       details jsonb, ip_address inet, user_agent text, event_ts)

Columns added to existing tables:
  - sessions.deleted_at  TIMESTAMPTZ nullable  (soft-delete support for erasure)

Notes:
  - users.naipunyam_id and users.deleted_at already exist from migration a1b2c3d4e5f6;
    no changes needed on the users table.
  - audit_log is NOT partitioned (pg_partman not confirmed on demo Neon DB;
    partitioning deferred to Sprint 6+ / production migration).
  - erasure_requests.status is VARCHAR(16) to match the LLD DDL. Allowed values:
    'pending' | 'completed' | 'failed' — enforced at application layer.

Revision ID: c7d8e9f0a1b2
Revises:     f9a2c1d3b4e7
Create Date: 2026-05-29 00:01:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c7d8e9f0a1b2"
down_revision: str | None = "f9a2c1d3b4e7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # sessions.deleted_at — soft-delete column for DPDP erasure
    # ------------------------------------------------------------------
    op.add_column(
        "sessions",
        sa.Column("deleted_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )

    # ------------------------------------------------------------------
    # erasure_requests
    # ------------------------------------------------------------------
    op.create_table(
        "erasure_requests",
        sa.Column(
            "request_id",
            sa.UUID(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("requested_by", sa.UUID(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        # status: pending | completed | failed — validated at application layer
        sa.Column(
            "status",
            sa.String(16),
            server_default="pending",
            nullable=False,
        ),
        sa.Column("scheduled_for", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("completed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("artifacts", JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_erasure_requests_user_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("request_id", name="pk_erasure_requests"),
    )
    op.create_index(
        "ix_erasure_requests_user_id",
        "erasure_requests",
        ["user_id"],
    )

    # ------------------------------------------------------------------
    # audit_log
    # NOT partitioned — keep simple for Sprint 5 (see docstring).
    # ------------------------------------------------------------------
    op.create_table(
        "audit_log",
        sa.Column(
            "event_id",
            sa.UUID(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("actor_id", sa.UUID(), nullable=True),
        sa.Column("actor_type", sa.String(16), nullable=True),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("resource_type", sa.String(32), nullable=True),
        sa.Column("resource_id", sa.UUID(), nullable=True),
        sa.Column("details", JSONB(), nullable=True),
        # INET is a PostgreSQL-native type; using op.execute to add the column
        # avoids needing a dialect-specific Column constructor here.
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column(
            "event_ts",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("event_id", name="pk_audit_log"),
    )

    # ip_address uses PostgreSQL INET type — add via raw SQL after table creation.
    op.execute("ALTER TABLE audit_log ADD COLUMN ip_address INET")

    op.create_index(
        "ix_audit_log_event_ts",
        "audit_log",
        ["event_ts"],
    )


def downgrade() -> None:
    # audit_log (standalone — no FK dependants)
    op.drop_index("ix_audit_log_event_ts", table_name="audit_log")
    op.drop_table("audit_log")

    # erasure_requests (FK to users — drop before removing the column)
    op.drop_index("ix_erasure_requests_user_id", table_name="erasure_requests")
    op.drop_table("erasure_requests")

    # sessions.deleted_at soft-delete column
    op.drop_column("sessions", "deleted_at")
