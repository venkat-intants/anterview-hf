"""feature_flags (platform feature toggles for the Super Admin console)

A tiny key/label/enabled table the super-admin console reads + toggles. Seeded
with the product's existing feature switches so the tab is populated on day one.

Revision ID: a1c2e3f4b5d6
Revises:     f8a1b2c3d4e5
Create Date: 2026-06-24 00:02:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a1c2e3f4b5d6"
down_revision: str | None = "f8a1b2c3d4e5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "feature_flags",
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("updated_by", sa.UUID(), nullable=True),
        sa.PrimaryKeyConstraint("key", name="pk_feature_flags"),
        sa.ForeignKeyConstraint(
            ["updated_by"], ["users.id"], name="fk_feature_flags_updated_by", ondelete="SET NULL"
        ),
    )
    # Seed the product's existing switches (idempotent).
    op.execute(
        """
        INSERT INTO feature_flags (key, label, description, enabled) VALUES
          ('interview_proctoring', 'Interview proctoring',
           'MediaPipe face/gaze proctoring during live interviews', true),
          ('multilingual', 'Multilingual interviews',
           'Allow Hindi / Telugu interview languages alongside English', true),
          ('voice_interruption', 'Voice interruption',
           'Let candidates interrupt the avatar mid-sentence', true),
          ('resume_ats_scoring', 'Resume ATS scoring',
           'Auto-score uploaded applicant resumes against the role', true),
          ('email_notifications', 'Email notifications',
           'Send transactional emails (invites, results) via Resend', false)
        ON CONFLICT (key) DO NOTHING
        """
    )


def downgrade() -> None:
    op.drop_table("feature_flags")
