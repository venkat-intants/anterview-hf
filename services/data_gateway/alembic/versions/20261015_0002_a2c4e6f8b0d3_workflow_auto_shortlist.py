"""workflows.auto_shortlist — an opt-in switch that removes the human shortlist gate
(plus enrolments.ats_injection_markers, which that switch depends on)

Revision ID: a2c4e6f8b0d3
Revises: f3a5c7e9b1d4
Create Date: 2026-10-15

THIS REVERSES A DELIBERATE DESIGN DECISION, AND SAYS SO. Migration
e7a9c1d3f5b8 (Group C workflows) created this table with two human gates —
shortlist confirmation and the final decision — and kept both OUT of the column
list on purpose:

    "The two human gates (shortlist confirmation, final decision) are not
    settings at all — they are absent from this table by design, so no
    configuration can switch them off (D-05)."

The same invariant is restated in workflows.update_settings and in
workflow_runner.on_shortlisted ("The runner never shortlists anyone itself").
This migration makes the FIRST of those two gates optional, at the product
owner's request: a hiring team running a high-volume role wants applicants who
clear the ATS bar to start the assessment on their own, instead of waiting in a
queue for someone to press a button they would press anyway.

WHAT STILL HOLDS, which is what keeps this inside D-05 rather than around it:

  * Auto-shortlist only ever ADVANCES. An applicant below the bar is left
    exactly where they were — still 'new', still in HR's queue, still
    shortlistable by hand. Nobody is held, rejected or hidden by this switch.
    D-05's actual promise is that nothing automated ends a candidacy, and that
    is untouched.
  * The FINAL decision gate is not touched and is not made settable. It stays
    absent from this table. A hire or a reject is still a person, with a reason
    recorded against their name (CLAUDE.md hard constraint 9: AI must never
    decide hiring outcomes).
  * The switch defaults OFF, so every workflow that exists today keeps its
    human gate until somebody turns it off on purpose.
  * Settings are draft-only (workflows.update_settings asserts a draft), so
    turning this on means a new workflow version, which goes through super-admin
    approval before it is published. Removing a human gate is itself reviewed
    by a human.

THE CHECK IS THE IMPORTANT PART. Three combinations are made unrepresentable
rather than merely discouraged:

  * no bar, or a bar of 0 — ``score >= 0`` admits every scored applicant,
    including one recorded as 0 because the scorer's reply had no "overall";
  * ``auto_assign_first_round`` off — applicants would be marked shortlisted
    and then have nothing started, stranded with no person watching, because
    the shortlist was the step a person used to see.

    NOT auto_shortlist OR (shortlist_ats_threshold IS NOT NULL
                           AND shortlist_ats_threshold >= 1
                           AND auto_assign_first_round)

THE ``IS NOT NULL`` IS NOT REDUNDANT. A CHECK passes when its expression is
TRUE *or NULL*, and ``NULL >= 1`` is NULL — so without it, auto-shortlist with
a NULL threshold would satisfy the constraint, which is the case it most needs
to refuse. (A first draft of this constraint made exactly that mistake.)

It is added plain, not NOT VALID: the column is new and defaults to false, so
every existing row satisfies it trivially. See docs/ACCEPTED-RISKS.md and the
"CI migrates an empty database" note — a narrowing change here later would need
the NOT VALID treatment.

ENROLMENTS.ATS_INJECTION_MARKERS. The resume scorer has always scanned the
CV it scores for prompt-injection markers, but its HTTP response model dropped
the result, so data_gateway never knew a score might have been talked up. With
a person reviewing every shortlist that mattered less; with auto-shortlist the
score alone starts an assessment. The scorer now returns the marker NAMES and
they are stored next to the score they describe, so the check always concerns
the CV that produced THIS number — not whatever CV the applicant row holds now
(a returning applicant's new CV lives on the enrolment, not the applicant).
NULL means "not on record" (scored before this column existed)
and is never read as clean. Nullable, no default, no backfill: nothing here
can fail against existing rows.

SCALE NOTE, because it is easy to get wrong in the code that reads this:
``shortlist_ats_threshold`` is stored 0-10 and ``enrolments.ats_overall`` is
0-100. Every existing reader compares ``ats_overall >= threshold * 10``
(requisition_dashboard, agents/watch_runner). The runner must do the same, or
auto-shortlist advances everyone.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "a2c4e6f8b0d3"
down_revision: str | None = "f3a5c7e9b1d4"
branch_labels: None = None
depends_on: None = None


def upgrade() -> None:
    op.add_column(
        "workflows",
        sa.Column(
            "auto_shortlist",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.create_check_constraint(
        "ck_workflows_auto_shortlist_needs_threshold",
        "workflows",
        "NOT auto_shortlist OR (shortlist_ats_threshold IS NOT NULL AND shortlist_ats_threshold >= 1 AND auto_assign_first_round)",
    )
    op.add_column(
        "enrolments",
        sa.Column("ats_injection_markers", postgresql.JSONB(), nullable=True),
    )


def downgrade() -> None:
    # Lossy, honestly: every workflow returns to a human shortlist gate, which is
    # the behaviour the table had before. There is nowhere else to keep the value.
    op.drop_column("enrolments", "ats_injection_markers")
    op.drop_constraint(
        "ck_workflows_auto_shortlist_needs_threshold", "workflows", type_="check"
    )
    op.drop_column("workflows", "auto_shortlist")
