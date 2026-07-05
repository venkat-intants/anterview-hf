"""resume_jd_columns

B-031 / B-032: Add resume and JD document storage columns.

users table — candidate resume:
  - resume_text   TEXT NULL  (extracted plain-text, populated by B-031 upload)
  - resume_s3_key TEXT NULL  (R2/S3 object key for the uploaded PDF)

jobs table — job description document:
  - jd_text   TEXT NULL  (extracted plain-text, populated by B-032 upload)
  - jd_s3_key TEXT NULL  (R2/S3 object key for the uploaded PDF)

Revision ID: b2c3d4e5f6a1
Revises:     f0a1b2c3d4e5
Create Date: 2026-05-29 00:05:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b2c3d4e5f6a1"
down_revision: str | None = "f0a1b2c3d4e5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # users: candidate resume storage
    # ------------------------------------------------------------------
    op.add_column("users", sa.Column("resume_text", sa.Text(), nullable=True))
    op.add_column("users", sa.Column("resume_s3_key", sa.Text(), nullable=True))

    # ------------------------------------------------------------------
    # jobs: JD document storage
    # ------------------------------------------------------------------
    op.add_column("jobs", sa.Column("jd_text", sa.Text(), nullable=True))
    op.add_column("jobs", sa.Column("jd_s3_key", sa.Text(), nullable=True))


def downgrade() -> None:
    # jobs — reverse order
    op.drop_column("jobs", "jd_s3_key")
    op.drop_column("jobs", "jd_text")

    # users — reverse order
    op.drop_column("users", "resume_s3_key")
    op.drop_column("users", "resume_text")
