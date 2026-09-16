"""Requisition approval and budget — PH3-B2.

Revision ID: e5a7c9d1f3b6
Revises: d4f6b8c0e2a5
Create Date: 2026-09-16

TWO STATE MACHINES, KEPT APART
``status`` (open / paused / closed) stays what it has always been: the
OPERATIONAL lifecycle of an opening. ``approval_status`` is the GOVERNANCE gate
and is a separate axis. They are not merged and ``approved`` does not imply
``open`` — an approved opening that HR has paused is paused, and a closed
opening does not become unapproved. Folding them into one column would have
produced states like "approved-but-paused" that the column could not express,
and the first thing anyone would have done is add a second column back.

WHY EXISTING REQUISITIONS ARE GRANDFATHERED AS APPROVED
Defaulting every row to ``draft`` would take every live opening off the public
web the moment this migration ran, because the publish gate now requires
approval. That is not a governance improvement, it is an outage. Rows that exist
today were created under the rules that existed today; they are marked
``approved`` with the requisition's own ``created_at``, and
``approval_note`` records that the grant was made by this migration rather than
by a person, so nobody later reads it as a decision somebody took.

New requisitions start at ``draft`` and must be approved before they can be
published. That is the point of the story.

WHY BUDGET IS FOUR COLUMNS AND NOT ONE
An amount without a currency is not a budget, and an amount without a basis is
ambiguous in the way that actually costs money: "50,00,000" against a
requisition with target_hires = 5 means something very different per hire than
in total. ``budget_basis`` and ``budget_period`` make the number mean one thing.

``target_hires`` is untouched. Headcount and money are different constraints and
the story says so explicitly.

BUDGET IS NEVER PUBLIC. Unlike ``salary_min``/``salary_max``, which have a
``salary_visible`` flag because a band may legitimately be advertised, there is
no circumstance in which a hiring budget belongs on a careers page. There is
therefore no visibility flag — the absence is the control — and
``tests/unit/test_ph3_requisition_approval.py`` asserts no public schema carries
these fields.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "e5a7c9d1f3b6"
down_revision: str | None = "d4f6b8c0e2a5"
branch_labels: str | None = None
depends_on: str | None = None

_GRANDFATHER_NOTE = (
    "Automatically approved when requisition approval was introduced "
    "(PH3-B2): this opening predates the approval gate."
)


def upgrade() -> None:
    # ── Approval ─────────────────────────────────────────────────────────
    op.add_column(
        "job_requisitions",
        sa.Column(
            "approval_status", sa.Text(), server_default=sa.text("'draft'"), nullable=False
        ),
    )
    op.add_column(
        "job_requisitions",
        sa.Column("submitted_for_approval_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.add_column(
        "job_requisitions", sa.Column("submitted_by_user_id", sa.Uuid(), nullable=True)
    )
    op.add_column(
        "job_requisitions",
        sa.Column("approval_decided_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.add_column(
        "job_requisitions",
        sa.Column("approval_decided_by_user_id", sa.Uuid(), nullable=True),
    )
    # The approver's words: a rejection reason, or a condition on an approval.
    op.add_column("job_requisitions", sa.Column("approval_note", sa.Text(), nullable=True))

    op.create_check_constraint(
        "ck_job_requisitions_approval_status",
        "job_requisitions",
        "approval_status IN ('draft','pending_approval','approved','rejected')",
    )
    # A decided requisition has a decision time, and an undecided one does not.
    # Without this the two could drift and "when was this approved?" would have
    # two answers, one of them wrong.
    op.create_check_constraint(
        "ck_job_requisitions_approval_decided",
        "job_requisitions",
        "(approval_status IN ('approved','rejected')) = (approval_decided_at IS NOT NULL)",
    )
    op.create_foreign_key(
        "fk_job_requisitions_submitted_by",
        "job_requisitions", "users", ["submitted_by_user_id"], ["id"], ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_job_requisitions_approved_by",
        "job_requisitions", "users", ["approval_decided_by_user_id"], ["id"],
        ondelete="SET NULL",
    )

    # ── Budget ───────────────────────────────────────────────────────────
    # BIGINT: an annual hiring budget for a large requisition exceeds a 32-bit
    # integer in rupees, and discovering that in production would mean an
    # overflow on the one field finance reads.
    op.add_column("job_requisitions", sa.Column("budget_amount", sa.BigInteger(), nullable=True))
    op.add_column("job_requisitions", sa.Column("budget_currency", sa.Text(), nullable=True))
    op.add_column("job_requisitions", sa.Column("budget_basis", sa.Text(), nullable=True))
    op.add_column("job_requisitions", sa.Column("budget_period", sa.Text(), nullable=True))
    op.add_column("job_requisitions", sa.Column("budget_notes", sa.Text(), nullable=True))

    op.create_check_constraint(
        "ck_job_requisitions_budget_amount",
        "job_requisitions",
        "budget_amount IS NULL OR budget_amount >= 0",
    )
    op.create_check_constraint(
        "ck_job_requisitions_budget_basis",
        "job_requisitions",
        "budget_basis IS NULL OR budget_basis IN ('per_hire','total')",
    )
    op.create_check_constraint(
        "ck_job_requisitions_budget_period",
        "job_requisitions",
        "budget_period IS NULL OR budget_period IN ('annual','monthly','one_time')",
    )
    # An amount with no currency or basis is a number nobody can act on. The
    # database refuses the half-filled form rather than leaving every reader to
    # guess what the number meant.
    op.create_check_constraint(
        "ck_job_requisitions_budget_complete",
        "job_requisitions",
        "budget_amount IS NULL"
        " OR (budget_currency IS NOT NULL AND budget_basis IS NOT NULL"
        "     AND budget_period IS NOT NULL)",
    )

    # ── Grandfather every opening that already exists ────────────────────
    op.execute(
        f"""
        UPDATE job_requisitions
           SET approval_status = 'approved',
               approval_decided_at = created_at,
               approval_note = '{_GRANDFATHER_NOTE}'
         WHERE deleted_at IS NULL
        """
    )
    # Soft-deleted rows too: undeleting one later must not resurrect it into a
    # state the CHECK constraint forbids.
    op.execute(
        f"""
        UPDATE job_requisitions
           SET approval_status = 'approved',
               approval_decided_at = COALESCE(approval_decided_at, created_at),
               approval_note = COALESCE(approval_note, '{_GRANDFATHER_NOTE}')
         WHERE deleted_at IS NOT NULL
        """
    )

    # The publish gate now reads approval_status, so the partial indexes that
    # serve the public board get it too. Without this they stay usable (their
    # predicate is a superset) but every board query filters rows the index
    # already knows are ineligible.
    op.drop_index("ix_job_requisitions_public_open", table_name="job_requisitions")
    op.create_index(
        "ix_job_requisitions_public_open",
        "job_requisitions",
        ["company_id"],
        unique=False,
        postgresql_where=sa.text(
            "public_apply_enabled AND status = 'open' AND deleted_at IS NULL"
            " AND approval_status = 'approved'"
        ),
    )
    op.drop_index("ix_job_requisitions_board", table_name="job_requisitions")
    op.create_index(
        "ix_job_requisitions_board",
        "job_requisitions",
        ["company_id", "department", "location", "created_at"],
        unique=False,
        postgresql_where=sa.text(
            "public_apply_enabled AND status = 'open' AND deleted_at IS NULL"
            " AND approval_status = 'approved'"
        ),
    )


def downgrade() -> None:
    op.drop_index("ix_job_requisitions_board", table_name="job_requisitions")
    op.create_index(
        "ix_job_requisitions_board",
        "job_requisitions",
        ["company_id", "department", "location", "created_at"],
        unique=False,
        postgresql_where=sa.text(
            "public_apply_enabled AND status = 'open' AND deleted_at IS NULL"
        ),
    )
    op.drop_index("ix_job_requisitions_public_open", table_name="job_requisitions")
    op.create_index(
        "ix_job_requisitions_public_open",
        "job_requisitions",
        ["company_id"],
        unique=False,
        postgresql_where=sa.text(
            "public_apply_enabled AND status = 'open' AND deleted_at IS NULL"
        ),
    )
    for name in (
        "ck_job_requisitions_budget_complete",
        "ck_job_requisitions_budget_period",
        "ck_job_requisitions_budget_basis",
        "ck_job_requisitions_budget_amount",
        "ck_job_requisitions_approval_decided",
        "ck_job_requisitions_approval_status",
    ):
        op.drop_constraint(name, "job_requisitions", type_="check")
    op.drop_constraint("fk_job_requisitions_approved_by", "job_requisitions", type_="foreignkey")
    op.drop_constraint("fk_job_requisitions_submitted_by", "job_requisitions", type_="foreignkey")
    for col in (
        "budget_notes", "budget_period", "budget_basis", "budget_currency", "budget_amount",
        "approval_note", "approval_decided_by_user_id", "approval_decided_at",
        "submitted_by_user_id", "submitted_for_approval_at", "approval_status",
    ):
        op.drop_column("job_requisitions", col)
