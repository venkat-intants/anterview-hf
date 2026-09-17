"""Interview kits for human interview rounds — PH4-A5.

Revision ID: e3a5c7d9f1b2
Revises: d2f4a6c8e0b1
Create Date: 2026-09-17

A kit is what an interviewer reads before and during the interview: the round's
instructions, notes from HR, and per criterion what to evaluate, what to look
for and suggested probes.

WHAT A KIT IS NOT: A RUBRIC
The evaluation criteria stay in ``round_criteria``, frozen at publish and
guarded by a trigger. The kit REFERS to them by ``competency_id`` and never
holds a copy — "do not duplicate the rubric" is enforced by the kit having
nowhere to put one. ``guidance`` keys are validated against the round's frozen
criteria in the application, so a kit cannot introduce a criterion either.

WHY A SEPARATE TABLE, EDITABLE AFTER PUBLISH
Guidance is not measurement. Improving a probe ("ask about trade-offs, not
tools") after the workflow is live changes how an interviewer runs the
conversation, not what the candidate is scored against — so it does not need a
new workflow version, and should not require one. Keeping kits out of
``workflow_rounds`` is what lets the published-workflow trigger stay absolute
while kits remain editable. ``workflows.clone_for_edit`` copies the kit forward
to the new version's rounds.

``guidance`` shape: ``{competency_id: {"what_to_evaluate": [..], "look_for": [..],
"probes": [..]}}``.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "e3a5c7d9f1b2"
down_revision: str | None = "d2f4a6c8e0b1"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "interview_kits",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("round_id", sa.Uuid(), nullable=False),
        sa.Column("instructions", sa.Text(), nullable=True),
        sa.Column("interviewer_notes", sa.Text(), nullable=True),
        sa.Column(
            "guidance", postgresql.JSONB(), nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("updated_by_user_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at", sa.TIMESTAMP(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id", name="pk_interview_kits"),
        # One kit per round.
        sa.UniqueConstraint("round_id", name="uq_interview_kits_round"),
        sa.ForeignKeyConstraint(
            ["round_id", "company_id"],
            ["workflow_rounds.id", "workflow_rounds.company_id"],
            name="fk_interview_kits_round", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_user_id"], ["users.id"],
            name="fk_interview_kits_updated_by", ondelete="SET NULL",
        ),
        sa.CheckConstraint(
            "instructions IS NULL OR char_length(instructions) <= 8000",
            name="ck_interview_kits_instructions_len",
        ),
        sa.CheckConstraint(
            "interviewer_notes IS NULL OR char_length(interviewer_notes) <= 8000",
            name="ck_interview_kits_notes_len",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(guidance) = 'object'", name="ck_interview_kits_guidance_object"
        ),
    )


def downgrade() -> None:
    op.drop_table("interview_kits")
