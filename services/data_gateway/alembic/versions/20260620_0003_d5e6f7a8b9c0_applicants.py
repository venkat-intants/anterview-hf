"""applicants (HR workflow — Phase 1: resume ATS screening)

An applicant is a candidate an HR manager is screening for a role. Scoped to a
company (multi-tenant). The role being screened for is denormalized onto the row
(target_job_title/level/jd) so Phase-1 screening needs no separate openings
table. The ATS score (overall + breakdown + strengths/concerns/recommendation)
is produced by feedback_billing's resume scorer and stored here.

Revision ID: d5e6f7a8b9c0
Revises:     c4d5e6f7a8b9
Create Date: 2026-06-20 00:03:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "d5e6f7a8b9c0"
down_revision: str | None = "c4d5e6f7a8b9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "applicants",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("company_id", sa.UUID(), nullable=False),
        sa.Column("created_by_user_id", sa.UUID(), nullable=True),
        sa.Column("full_name", sa.Text(), nullable=False),
        sa.Column("email", sa.Text(), nullable=True),
        # Role being screened for (denormalized — no openings table in Phase 1).
        sa.Column("target_job_title", sa.Text(), nullable=False),
        sa.Column("target_level", sa.Text(), server_default="mid", nullable=False),
        sa.Column("target_jd_text", sa.Text(), nullable=True),
        # Resume (PII — never logged).
        sa.Column("resume_text", sa.Text(), nullable=True),
        sa.Column("resume_s3_key", sa.Text(), nullable=True),
        # ATS score (from feedback_billing resume scorer). NULL until scored.
        sa.Column("ats_overall", sa.SmallInteger(), nullable=True),
        sa.Column("ats_breakdown", JSONB(), nullable=True),
        sa.Column("ats_strengths", JSONB(), nullable=True),
        sa.Column("ats_concerns", JSONB(), nullable=True),
        sa.Column("ats_recommendation", sa.Text(), nullable=True),
        sa.Column("ats_summary", sa.Text(), nullable=True),
        # Pipeline status: new | shortlisted | rejected.
        sa.Column("status", sa.Text(), server_default="new", nullable=False),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.Column("deleted_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_applicants"),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"],
            name="fk_applicants_company", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["users.id"],
            name="fk_applicants_created_by", ondelete="SET NULL",
        ),
    )
    # Tenant-scoped query indexes (every read filters by company_id first).
    op.create_index("idx_applicants_company", "applicants", ["company_id"])
    op.create_index(
        "idx_applicants_company_score", "applicants", ["company_id", "ats_overall"]
    )


def downgrade() -> None:
    op.drop_index("idx_applicants_company_score", table_name="applicants")
    op.drop_index("idx_applicants_company", table_name="applicants")
    op.drop_table("applicants")
