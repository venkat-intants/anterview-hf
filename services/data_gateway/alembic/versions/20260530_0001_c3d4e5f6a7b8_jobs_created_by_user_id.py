"""jobs_created_by_user_id

Add ``created_by_user_id`` nullable UUID column to ``jobs``.

Purpose: distinguish user-created "practice" jobs (created_by_user_id IS NOT NULL)
from public/seeded jobs (created_by_user_id IS NULL).  GET /jobs only surfaces
public jobs; users address their custom jobs directly via the UUID returned by
POST /jobs.

jobs table:
  - created_by_user_id  UUID NULL  FK → users.id ON DELETE SET NULL

Revision ID: c3d4e5f6a7b8
Revises:     b2c3d4e5f6a1
Create Date: 2026-05-30 00:01:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c3d4e5f6a7b8"
down_revision: str | None = "b2c3d4e5f6a1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Add the nullable column first (safe to do before the FK constraint).
    op.add_column(
        "jobs",
        sa.Column("created_by_user_id", sa.UUID(), nullable=True),
    )

    # FK constraint — ON DELETE SET NULL so deleting a user orphans the job
    # (turns it public) rather than cascading a hard delete of the job row.
    op.create_foreign_key(
        "fk_jobs_created_by_user_id",
        "jobs",
        "users",
        ["created_by_user_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # Index to make "jobs owned by user X" queries efficient.
    op.create_index(
        "ix_jobs_created_by_user_id",
        "jobs",
        ["created_by_user_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_jobs_created_by_user_id", table_name="jobs")
    op.drop_constraint("fk_jobs_created_by_user_id", "jobs", type_="foreignkey")
    op.drop_column("jobs", "created_by_user_id")
