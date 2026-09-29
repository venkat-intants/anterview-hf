"""PH3-B4b: a reapplication is staged until the address is proven.

Revision ID: a1c3e5f7b9d2
Revises: f2a4c6e8b0d3
Create Date: 2026-09-29

THE GAP THIS CLOSES
`POST /apply/{id}` and `POST /apply/draft/submit` are anonymous by necessity —
anyone may apply — and they identify a person by an email address typed into a
public form. The requisition id is documented as not a secret. So the address
is a claim, not a fact, and `app/apply_activation.py` says so in as many words:
"Anyone can type anyone's address into an application form."

That rule was applied to the `applicants.*` columns and stopped there. When
the reapplication rule started letting a rejected application be made live
again, the same unverified address became the key to mutating an EXISTING
enrolment: a security review found that one anonymous request could move a real
person's status back to `new`, overwrite the screening answers they had
already given, attach an attacker-chosen PDF as "the CV this application was
submitted with", spend an override HR had granted them, and — for a candidate
who had never opted in — create a talent-pool consent with the sender's own IP
recorded as evidence. `final_decision.py` refuses an AUTHENTICATED, AUTHORISED
HR manager from hiring over a rejection on the grounds that reopening someone
is "a separate decision this does not make on anyone's behalf"; a control that
refuses the authorised path while an anonymous one clears it is not a control.

WHAT CHANGES
The application is still accepted anonymously — nobody is turned away, and the
cooldown and override rules still decide whether it is accepted at all. What
changes is that it no longer takes effect on submission. It is STAGED on the
rejected enrolment and applied only when the person proves the address by
following a link emailed to it.

1. `enrolments` gains three columns holding the pending attempt:
   `reapply_requested_at`, `reapply_resume_s3_key` and `reapply_answers`.
   The CV key is a column rather than a loose object on purpose: erasure
   collects a person's resume objects by reading the columns that name them,
   so an unnamed object has no deletion path (DPDP §12).
2. `auth_tokens.kind` admits `'reapply_confirm'`. A separate kind from
   `password_reset` because it is a separate thing — and because
   `stage_activation_email` mints nothing for a user who has already claimed
   their account, which would have left an already-activated candidate's
   reapplication pending for ever.

WHAT IS DELIBERATELY NOT HERE
No backfill. A staged reapplication is a thing that starts existing now; there
are no historical rows to interpret, and inventing `reapply_requested_at` for
past reapplications would put a claim in a column whose purpose is to record
that somebody proved something.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a1c3e5f7b9d2"
down_revision = "f2a4c6e8b0d3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "enrolments",
        sa.Column("reapply_requested_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "enrolments", sa.Column("reapply_resume_s3_key", sa.Text(), nullable=True)
    )
    op.add_column(
        "enrolments",
        sa.Column("reapply_answers", sa.dialects.postgresql.JSONB(), nullable=True),
    )
    # Partial, because the rows that matter are the few awaiting confirmation —
    # the confirm path looks one up by token, and the retention sweep will want
    # to find the stale ones.
    op.create_index(
        "ix_enrolments_reapply_pending",
        "enrolments",
        ["reapply_requested_at"],
        postgresql_where=sa.text("reapply_requested_at IS NOT NULL"),
    )

    op.drop_constraint("ck_auth_tokens_kind", "auth_tokens", type_="check")
    op.create_check_constraint(
        "ck_auth_tokens_kind",
        "auth_tokens",
        "kind IN ('password_reset','email_verify','reapply_confirm')",
    )


def downgrade() -> None:
    # A pending reapplication cannot survive this: the columns holding it go.
    # Refuse rather than silently discard somebody's second attempt — the same
    # stance the D4 downgrade takes about task rounds.
    stuck = op.get_bind().scalar(
        sa.text(
            "SELECT count(*) FROM enrolments WHERE reapply_requested_at IS NOT NULL"
        )
    )
    if stuck:
        raise RuntimeError(
            f"{stuck} reapplication(s) are waiting to be confirmed. Downgrading would "
            "discard them. Confirm or clear them first."
        )

    op.drop_constraint("ck_auth_tokens_kind", "auth_tokens", type_="check")
    op.create_check_constraint(
        "ck_auth_tokens_kind",
        "auth_tokens",
        "kind IN ('password_reset','email_verify')",
    )
    op.drop_index("ix_enrolments_reapply_pending", table_name="enrolments")
    op.drop_column("enrolments", "reapply_answers")
    op.drop_column("enrolments", "reapply_resume_s3_key")
    op.drop_column("enrolments", "reapply_requested_at")
