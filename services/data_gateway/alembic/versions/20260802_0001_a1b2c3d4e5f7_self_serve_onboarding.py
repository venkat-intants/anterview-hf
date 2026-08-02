"""self-serve onboarding + practice plan

Adds onboarding columns to ``users`` for the SELF-SERVE journey (a student
practising on their own, as opposed to an HR-invited applicant):

  onboarding_goal          — why they are here: 'campus_placement' |
                             'first_job' | 'switching_field' |
                             'interview_soon' | 'general_practice'
  target_role              — the role they are practising for, free text.
                             Fed to shared.intelligence to derive the
                             competencies their practice plan is built from.
  target_level             — 'entry' | 'mid' | 'senior'
  onboarding_completed_at  — set when they finish the wizard
  onboarding_skipped_at    — set when they dismiss it; NULL in both columns
                             means "never shown", which is what triggers the
                             first-login redirect

Why new columns rather than reusing ``desired_roles``/``headline``: those are
free-text profile display fields a user edits for other humans to read.
``target_role`` is a MACHINE input — it is classified by the role engine and
drives question planning and scoring — so overloading a display field would
mean a cosmetic profile edit silently re-targeting someone's practice plan.

All nullable, no backfill: existing users simply have NULLs and are treated as
"onboarding never shown". Existing HR/admin users are unaffected — the wizard
is only offered to users with no privileged role.

Revision ID: a1b2c3d4e5f7
Revises:     f7a9b1c3d5e7
Create Date: 2026-08-02 00:01:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a1b2c3d4e5f7"
down_revision: str | None = "f7a9b1c3d5e7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_TEXT_COLUMNS = (
    "onboarding_goal",
    "target_role",
    "target_level",
)

_TIMESTAMP_COLUMNS = (
    "onboarding_completed_at",
    "onboarding_skipped_at",
)


def upgrade() -> None:
    for col in _TEXT_COLUMNS:
        op.add_column("users", sa.Column(col, sa.Text(), nullable=True))
    for col in _TIMESTAMP_COLUMNS:
        op.add_column(
            "users", sa.Column(col, sa.TIMESTAMP(timezone=True), nullable=True)
        )


def downgrade() -> None:
    for col in reversed(_TIMESTAMP_COLUMNS):
        op.drop_column("users", col)
    for col in reversed(_TEXT_COLUMNS):
        op.drop_column("users", col)
