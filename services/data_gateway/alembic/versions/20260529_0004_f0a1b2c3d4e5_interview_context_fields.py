"""interview_context_fields

B-033: Add interview context fields to enrich candidate profiles and job roles
       for smarter LLM-driven interviewing.

users table — candidate profile enrichment:
  - linkedin_url  TEXT NULL
  - github_url    TEXT NULL

jobs table — role context for smarter interviewing:
  - company_name    TEXT NULL
  - department      TEXT NULL
  - interview_type  VARCHAR(16) NOT NULL DEFAULT 'screening'
    allowed values: 'screening' | 'technical' | 'hr'

Revision ID: f0a1b2c3d4e5
Revises:     e9f0a1b2c3d4
Create Date: 2026-05-29 00:04:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f0a1b2c3d4e5"
down_revision: str | None = "e9f0a1b2c3d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # users: candidate profile enrichment
    # ------------------------------------------------------------------
    op.add_column("users", sa.Column("linkedin_url", sa.Text(), nullable=True))
    op.add_column("users", sa.Column("github_url", sa.Text(), nullable=True))

    # ------------------------------------------------------------------
    # jobs: role context for smarter interviewing
    # ------------------------------------------------------------------
    op.add_column("jobs", sa.Column("company_name", sa.Text(), nullable=True))
    op.add_column("jobs", sa.Column("department", sa.Text(), nullable=True))
    # interview_type: 'screening' | 'technical' | 'hr'
    # NOT NULL with a server-side default so existing rows get 'screening'.
    op.add_column(
        "jobs",
        sa.Column(
            "interview_type",
            sa.String(16),
            server_default="screening",
            nullable=False,
        ),
    )


def downgrade() -> None:
    # jobs — remove in reverse column-addition order
    op.drop_column("jobs", "interview_type")
    op.drop_column("jobs", "department")
    op.drop_column("jobs", "company_name")

    # users
    op.drop_column("users", "github_url")
    op.drop_column("users", "linkedin_url")
