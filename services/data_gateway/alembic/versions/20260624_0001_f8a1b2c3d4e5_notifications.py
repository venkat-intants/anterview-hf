"""notifications (in-app notification feed for the header bell)

A lightweight per-user notification feed. Rows are produced by event handlers
(HR invite sent, interview completed, welcome on registration, …) and consumed
by the GET /notifications endpoint that backs the AppShell bell. read_at NULL =
unread; the bell badge counts unread rows.

Revision ID: f8a1b2c3d4e5
Revises:     a7b8c9d0e1f2
Create Date: 2026-06-24 00:01:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f8a1b2c3d4e5"
down_revision: str | None = "a7b8c9d0e1f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "notifications",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        # welcome | invite_sent | interview_completed | applicant_scored | decision | system
        sa.Column("kind", sa.Text(), server_default="system", nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("link", sa.Text(), nullable=True),
        sa.Column("read_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_notifications"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_notifications_user", ondelete="CASCADE"
        ),
    )
    # Newest-first per user (the list query).
    op.create_index(
        "idx_notifications_user_created",
        "notifications",
        ["user_id", sa.text("created_at DESC")],
    )
    # Unread badge count (partial index keeps it tiny).
    op.create_index(
        "idx_notifications_unread",
        "notifications",
        ["user_id"],
        postgresql_where=sa.text("read_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("idx_notifications_unread", table_name="notifications")
    op.drop_index("idx_notifications_user_created", table_name="notifications")
    op.drop_table("notifications")
