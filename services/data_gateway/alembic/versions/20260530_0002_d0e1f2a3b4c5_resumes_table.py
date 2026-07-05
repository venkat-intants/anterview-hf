"""resumes_table

Area 3 (UI redesign v2): Add ``resumes`` table for resume version history.

Instead of overwriting one column on ``users``, each upload now creates a new
row here.  The ``users.resume_text`` and ``users.resume_s3_key`` columns are
preserved and kept in sync with the ``is_current=true`` row so the B-033
enrichment path (which reads ``users.resume_text``) continues to work without
any changes.

resumes table:
  - id            UUID PK
  - user_id       UUID NOT NULL FK → users.id ON DELETE CASCADE (indexed)
  - filename      TEXT NOT NULL  (original upload filename)
  - resume_text   TEXT NOT NULL  (extracted plain text)
  - resume_s3_key TEXT NOT NULL  (versioned S3 key, e.g. resumes/{user}/{id}.pdf)
  - is_current    BOOL NOT NULL DEFAULT false
  - uploaded_at   TIMESTAMPTZ NOT NULL DEFAULT now()
  - created_at    TIMESTAMPTZ NOT NULL DEFAULT now()

Revision ID: d0e1f2a3b4c5
Revises:     c3d4e5f6a7b8
Create Date: 2026-05-30 00:02:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d0e1f2a3b4c5"
down_revision: str | None = "c3d4e5f6a7b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "resumes",
        sa.Column(
            "id",
            sa.UUID(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("resume_text", sa.Text(), nullable=False),
        sa.Column("resume_s3_key", sa.Text(), nullable=False),
        sa.Column("is_current", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "uploaded_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_resumes"),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_resumes_user_id",
            ondelete="CASCADE",
        ),
    )

    op.create_index("ix_resumes_user_id", "resumes", ["user_id"])
    # Partial unique index: at most one is_current=true per user.
    op.create_index(
        "uix_resumes_user_current",
        "resumes",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("is_current = true"),
    )


def downgrade() -> None:
    op.drop_index("uix_resumes_user_current", table_name="resumes")
    op.drop_index("ix_resumes_user_id", table_name="resumes")
    op.drop_table("resumes")
