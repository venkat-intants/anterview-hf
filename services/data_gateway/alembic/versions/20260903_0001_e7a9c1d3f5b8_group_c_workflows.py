"""Group C — versioned workflows, typed rounds, frozen criteria and round results

The feature
-----------
Every job gets its own hiring workflow, configured by HR: the rounds, their
order, what each round assesses, and what it takes to advance. Candidates who
apply to that role are enrolled into it, and the system advances them.

Four tables and four columns.

``workflows``        one per requisition version. Carries the per-workflow
                     automation settings (C9), replacing today's arrangement
                     where the single existing auto-advance hides behind two
                     flags on two different screens and ships disabled.
``workflow_rounds``  ordered, typed, each with its own advance threshold and an
                     explicit ``on_pass_next_round_id``.
``round_criteria``   what a round assesses — a *frozen copy* of the competencies
                     selected from the role profile.
``round_results``    what a candidate scored, in two layers (C8).

Why criteria are frozen rather than referenced
----------------------------------------------
``RoleProfile`` is derived at runtime by ``shared/intelligence`` and is **not
persisted** — only its ``profile_id`` is stamped onto a scorecard. Its
competencies can therefore change between two derivations: Gemini refines the
baseline differently, or the taxonomy is updated. A published workflow that
merely *referenced* competency ids would silently start assessing something
else, and a candidate who sat the round last week would have been measured
against a rubric that no longer exists.

So the authoring step copies id, name, kind and weight into ``round_criteria``,
and the workflow records which profile it was built from for traceability. This
is what makes D-05's versioning promise real: a published workflow is immutable,
including the thing it measures.

Why an explicit next-round pointer
----------------------------------
D-04: execution is linear, but rounds store ``on_pass_next_round_id`` rather
than relying on ``position``. That costs nothing now and means branching later
is a data change rather than a migration. A NULL pointer means "this was the
last round" — the candidate goes to the final human decision, never to an
automatic outcome.

Revision ID: e7a9c1d3f5b8
Revises:     d6f8a0c2e4b7
Create Date: 2026-09-03 00:01:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "e7a9c1d3f5b8"
down_revision: str | None = "d6f8a0c2e4b7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ROUND_KINDS = "('mcq','coding','ai_interview','human_review')"


def upgrade() -> None:
    # ── workflows ─────────────────────────────────────────────────────────
    op.create_table(
        "workflows",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("company_id", sa.UUID(), nullable=False),
        sa.Column("requisition_id", sa.UUID(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'draft'"), nullable=False),
        sa.Column("name", sa.Text(), nullable=True),
        # Traceability: which role model the criteria were copied from. Not a
        # foreign key, because role profiles are derived rather than stored.
        sa.Column("role_profile_id", sa.Text(), nullable=True),
        sa.Column("domain_family", sa.Text(), nullable=True),
        sa.Column("profile_source", sa.Text(), nullable=True),
        # ── C9: automation settings, per workflow rather than global ──
        # Defaults are ON because each is mechanical. The two human gates
        # (shortlist confirmation, final decision) are not settings at all —
        # they are absent from this table by design, so no configuration can
        # switch them off (D-05).
        sa.Column(
            "auto_score_on_apply", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        sa.Column(
            "auto_assign_first_round", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        sa.Column(
            "auto_advance_rounds", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        sa.Column(
            "reminders_enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        # Sets the pre-selection for the shortlist queue. Never the decision:
        # a person still presses the button.
        sa.Column("shortlist_ats_threshold", sa.SmallInteger(), nullable=True),
        # How far below a round's threshold still lands in the review queue
        # rather than simply stopping. 0 = every miss is held anyway; held is
        # never a rejection either way.
        sa.Column(
            "hold_band", sa.SmallInteger(), server_default=sa.text("10"), nullable=False
        ),
        # A held candidate stops consuming AI by default. Turning this on lets
        # everyone complete every round for a fuller picture, at roughly
        # Rs.10-12 per interview — material against the per-session cap.
        sa.Column(
            "continue_on_hold", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("created_by_user_id", sa.UUID(), nullable=True),
        sa.Column("published_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_workflows"),
        sa.UniqueConstraint("id", "company_id", name="uq_workflows_id_company"),
        sa.UniqueConstraint(
            "requisition_id", "version", name="uq_workflows_requisition_version"
        ),
        sa.ForeignKeyConstraint(
            ["requisition_id", "company_id"],
            ["job_requisitions.id", "job_requisitions.company_id"],
            name="fk_workflows_requisition", ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "status IN ('draft','published','archived')", name="ck_workflows_status"
        ),
        sa.CheckConstraint("version > 0", name="ck_workflows_version"),
        sa.CheckConstraint(
            "shortlist_ats_threshold IS NULL"
            " OR (shortlist_ats_threshold >= 0 AND shortlist_ats_threshold <= 10)",
            name="ck_workflows_shortlist_threshold",
        ),
        sa.CheckConstraint(
            "hold_band >= 0 AND hold_band <= 100", name="ck_workflows_hold_band"
        ),
    )
    # Exactly one published workflow per requisition. Enrolled candidates finish
    # on the version they started; new applicants join the published one.
    op.create_index(
        "uq_workflows_one_published",
        "workflows",
        ["requisition_id"],
        unique=True,
        postgresql_where=sa.text("status = 'published' AND deleted_at IS NULL"),
    )

    # ── workflow_rounds ───────────────────────────────────────────────────
    op.create_table(
        "workflow_rounds",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("company_id", sa.UUID(), nullable=False),
        sa.Column("workflow_id", sa.UUID(), nullable=False),
        sa.Column("position", sa.SmallInteger(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        # ALWAYS a percentage, 0-100, whatever the round kind. Scores arrive on
        # their round's own scale (an interview composite is out of 10) and are
        # converted before comparison — see workflow_runner.INTERVIEW_SCORE_MAX.
        # Nullable for human_review, where a person decides rather than a number.
        sa.Column("pass_threshold", sa.Numeric(5, 2), nullable=True),
        sa.Column("time_limit_seconds", sa.Integer(), nullable=True),
        sa.Column("deadline_days", sa.SmallInteger(), server_default=sa.text("7"), nullable=False),
        # NULL = last round; the candidate goes to the final human decision.
        sa.Column("on_pass_next_round_id", sa.UUID(), nullable=True),
        # Where the round's content comes from. For mcq/coding this points at
        # the existing exam machinery, which already has authoring, generation,
        # CSV import and graders — Group C reuses it rather than rebuilding it.
        sa.Column("exam_round_id", sa.UUID(), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_workflow_rounds"),
        sa.UniqueConstraint("id", "company_id", name="uq_workflow_rounds_id_company"),
        sa.ForeignKeyConstraint(
            ["workflow_id", "company_id"], ["workflows.id", "workflows.company_id"],
            name="fk_workflow_rounds_workflow", ondelete="CASCADE",
        ),
        # Self-reference for the next-round pointer. SET NULL rather than
        # CASCADE: deleting a round must not delete the rest of the chain, it
        # must leave the predecessor pointing at "end of workflow" so the
        # candidate reaches the human decision rather than vanishing.
        sa.ForeignKeyConstraint(
            ["on_pass_next_round_id"], ["workflow_rounds.id"],
            name="fk_workflow_rounds_next", ondelete="SET NULL",
        ),
        sa.CheckConstraint(f"kind IN {ROUND_KINDS}", name="ck_workflow_rounds_kind"),
        sa.CheckConstraint(
            "pass_threshold IS NULL OR (pass_threshold >= 0 AND pass_threshold <= 100)",
            name="ck_workflow_rounds_threshold",
        ),
        sa.CheckConstraint("position >= 0", name="ck_workflow_rounds_position"),
        # A round cannot point at itself — the cheapest possible cycle.
        sa.CheckConstraint(
            "on_pass_next_round_id IS NULL OR on_pass_next_round_id <> id",
            name="ck_workflow_rounds_no_self_loop",
        ),
    )
    op.create_index(
        "uq_workflow_rounds_position",
        "workflow_rounds",
        ["workflow_id", "position"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    # ── round_criteria ────────────────────────────────────────────────────
    op.create_table(
        "round_criteria",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("company_id", sa.UUID(), nullable=False),
        sa.Column("round_id", sa.UUID(), nullable=False),
        # Frozen copy from the role profile — see the module docstring. Not a
        # foreign key: there is no table to point at, and that is the point.
        sa.Column("competency_id", sa.Text(), nullable=False),
        sa.Column("competency_name", sa.Text(), nullable=False),
        sa.Column("competency_kind", sa.Text(), nullable=True),
        sa.Column("weight", sa.Numeric(4, 3), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_round_criteria"),
        sa.ForeignKeyConstraint(
            ["round_id"], ["workflow_rounds.id"],
            name="fk_round_criteria_round", ondelete="CASCADE",
        ),
        sa.UniqueConstraint("round_id", "competency_id", name="uq_round_criteria_round_comp"),
        sa.CheckConstraint("weight > 0 AND weight <= 1", name="ck_round_criteria_weight"),
    )

    # ── round_results ─────────────────────────────────────────────────────
    op.create_table(
        "round_results",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("company_id", sa.UUID(), nullable=False),
        sa.Column("enrolment_id", sa.UUID(), nullable=False),
        sa.Column("round_id", sa.UUID(), nullable=False),
        # The exam attempt or interview session this came from.
        sa.Column("attempt_ref", sa.UUID(), nullable=True),
        sa.Column("score", sa.Numeric(6, 2), nullable=True),
        sa.Column("max_score", sa.Numeric(6, 2), nullable=True),
        sa.Column("percent", sa.Numeric(5, 2), nullable=True),
        # Advanced, or held. Never "rejected" — no AI-graded round may end a
        # candidacy (D-05), so this is a progression flag, not an outcome.
        sa.Column("passed", sa.Boolean(), nullable=True),
        # C8 upper layer: the evaluation. Per-criterion scores with evidence,
        # keyed by competency_id. This is what decides progression.
        sa.Column("criterion_scores", postgresql.JSONB(), nullable=True),
        # C8 lower layer: the four canonical axes, interview rounds only. Kept
        # frozen so composites stay comparable across roles and cohorts (D-02).
        sa.Column("axes", postgresql.JSONB(), nullable=True),
        sa.Column("graded_by", sa.Text(), nullable=False),
        sa.Column("grader_user_id", sa.UUID(), nullable=True),
        sa.Column("evidence", sa.Text(), nullable=True),
        # A retake supersedes rather than overwrites: the earlier attempt stays
        # readable, which an appeal or an audit will ask for.
        sa.Column("superseded_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_round_results"),
        sa.ForeignKeyConstraint(
            ["enrolment_id", "company_id"], ["enrolments.id", "enrolments.company_id"],
            name="fk_round_results_enrolment", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["round_id", "company_id"],
            ["workflow_rounds.id", "workflow_rounds.company_id"],
            name="fk_round_results_round", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["grader_user_id"], ["users.id"],
            name="fk_round_results_grader", ondelete="SET NULL",
        ),
        sa.CheckConstraint(
            "graded_by IN ('deterministic','ai','human')", name="ck_round_results_graded_by"
        ),
    )
    op.create_index(
        "uq_round_results_live",
        "round_results",
        ["enrolment_id", "round_id"],
        unique=True,
        postgresql_where=sa.text("superseded_at IS NULL"),
    )
    op.create_index("ix_round_results_enrolment", "round_results", ["enrolment_id"])

    # ── enrolment linkage (C5 / C6 / C7) ──────────────────────────────────
    op.add_column("enrolments", sa.Column("workflow_id", sa.UUID(), nullable=True))
    op.add_column("enrolments", sa.Column("current_round_id", sa.UUID(), nullable=True))
    op.add_column("enrolments", sa.Column("held_at", sa.TIMESTAMP(timezone=True), nullable=True))
    op.add_column("enrolments", sa.Column("held_reason", sa.Text(), nullable=True))
    op.create_foreign_key(
        "fk_enrolments_workflow", "enrolments", "workflows",
        ["workflow_id"], ["id"], ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_enrolments_current_round", "enrolments", "workflow_rounds",
        ["current_round_id"], ["id"], ondelete="SET NULL",
    )
    op.create_index(
        "ix_enrolments_workflow_round", "enrolments", ["workflow_id", "current_round_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_enrolments_workflow_round", table_name="enrolments")
    op.drop_constraint("fk_enrolments_current_round", "enrolments", type_="foreignkey")
    op.drop_constraint("fk_enrolments_workflow", "enrolments", type_="foreignkey")
    for col in ("held_reason", "held_at", "current_round_id", "workflow_id"):
        op.drop_column("enrolments", col)
    op.drop_index("ix_round_results_enrolment", table_name="round_results")
    op.drop_index("uq_round_results_live", table_name="round_results")
    op.drop_table("round_results")
    op.drop_table("round_criteria")
    op.drop_index("uq_workflow_rounds_position", table_name="workflow_rounds")
    op.drop_table("workflow_rounds")
    op.drop_index("uq_workflows_one_published", table_name="workflows")
    op.drop_table("workflows")
