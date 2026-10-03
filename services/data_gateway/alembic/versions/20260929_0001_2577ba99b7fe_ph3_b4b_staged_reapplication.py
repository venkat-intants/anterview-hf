"""PH3-B4b: a reapplication is staged until the address is proven.

Revision ID: 2577ba99b7fe
Revises: d9f1b3c5e7a2
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
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "2577ba99b7fe"
# REBASED onto main's question-bank migration when main was merged in on
# 2026-10-04. Both this migration and `e1c3f5a7b9d2` were written against
# `d9f1b3c5e7a2`, so merging the two branches produced two alembic heads and
# `alembic upgrade head` refused to run. Only this branch's migrations were
# repointed — rewriting somebody else's migration to resolve your own branch's
# collision is the wrong way round, and the chain reads the same either way.
down_revision = "e1c3f5a7b9d2"
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
        sa.Column("reapply_answers", JSONB(), nullable=True),
    )
    # THE TOKEN IS BOUND TO THE ATTEMPT, not merely to the person.
    #
    # A security re-audit found that a token scoped to `applicants.user_id`
    # applies EVERYTHING that person has staged — across companies — so an
    # attacker who knows a victim's address could keep re-staging until the
    # victim followed their own link, and the victim's confirmation would then
    # authenticate the attacker's CV and answers. The hash of the outstanding
    # link lives on the row it will act on, so a confirmation can only ever
    # apply the one attempt that link was minted for, and re-staging replaces
    # the hash — which invalidates the previous link by construction.
    op.add_column(
        "enrolments", sa.Column("reapply_token_hash", sa.Text(), nullable=True)
    )
    op.create_index(
        "ix_enrolments_reapply_token",
        "enrolments",
        ["reapply_token_hash"],
        unique=True,
        postgresql_where=sa.text("reapply_token_hash IS NOT NULL"),
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
    bind = op.get_bind()
    stuck = bind.scalar(
        sa.text(
            "SELECT count(*) FROM enrolments WHERE reapply_requested_at IS NOT NULL"
        )
    )
    if stuck:
        raise RuntimeError(
            f"{stuck} reapplication(s) are waiting to be confirmed. Downgrading would "
            "discard them. Confirm or clear them first."
        )

    # CONSUMED tokens block the downgrade too, and the guard above cannot see
    # them. Confirming a reapplication clears `reapply_requested_at` but leaves
    # its `auth_tokens` row behind with kind='reapply_confirm' — nothing
    # deletes it, and the erasure register deliberately leaves that table
    # alone. Narrowing the CHECK below then fails on rows the guard just
    # declared absent, aborting the downgrade half-applied.
    #
    # These are spent credentials for a flow that is being removed, so they are
    # deleted rather than refused: keeping them would mean a downgrade is
    # impossible for ever after the first confirmation.
    spent = bind.execute(
        sa.text("DELETE FROM auth_tokens WHERE kind = 'reapply_confirm'")
    ).rowcount
    if spent:
        print(f"  removed {spent} spent reapply_confirm token(s)")  # noqa: T201

    op.drop_constraint("ck_auth_tokens_kind", "auth_tokens", type_="check")
    op.create_check_constraint(
        "ck_auth_tokens_kind",
        "auth_tokens",
        "kind IN ('password_reset','email_verify')",
    )
    op.drop_index("ix_enrolments_reapply_token", table_name="enrolments")
    op.drop_column("enrolments", "reapply_token_hash")
    op.drop_index("ix_enrolments_reapply_pending", table_name="enrolments")
    op.drop_column("enrolments", "reapply_answers")
    op.drop_column("enrolments", "reapply_resume_s3_key")
    op.drop_column("enrolments", "reapply_requested_at")
