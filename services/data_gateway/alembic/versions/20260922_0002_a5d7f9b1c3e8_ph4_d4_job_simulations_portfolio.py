"""Job simulations and portfolio rounds — PH4-D4.

Revision ID: a5d7f9b1c3e8
Revises: f4c6e8a0b2d7
Create Date: 2026-09-22

WHAT THIS ADDS
Two new workflow round kinds, ``job_simulation`` and ``portfolio``, evaluated
by a person against the round's frozen ``round_criteria`` — never by a model,
never automatically. ``round_tasks`` is the HR-authored configuration (one row
per round): a brief, a list of structured items, and — for portfolio —
artifact settings (how many, files or links, which link domains). HR may also
attach reference materials (``round_task_materials``).

A candidate reaches their task through the same magic-link pattern the exam
and offer flows already use: ``task_submissions`` is the lifecycle row (one
live row per (enrolment, round), superseded on re-issue, never edited once
closed), and ``task_responses`` holds what they actually wrote, linked or
uploaded, frozen the moment the parent submission is no longer open.
``task_events`` is the append-only history of both.

THE KIND CHECK AND ``enrolment_awaits_human``
``ck_workflow_rounds_kind`` grows from four values to six. Downgrading back to
four is refused while any round still uses one of the new kinds — the
constraint would otherwise reject rows that already exist, and a working
downgrade should say why rather than fail with a bare integrity error.

``enrolment_awaits_human(status, round_id)`` keeps its two-argument signature
(open decision 17) and widens its round check to
``kind IN ('human_review','job_simulation','portfolio')``. Every existing
caller — the decision queue, the watcher, the requisition counts, the O1 stage
SLA query — needed no change at all.

REUSED, NOT REINVENTED
``round_tasks`` and ``round_task_materials`` attach the existing
``workflow_children_immutable()`` trigger unchanged (migration
``a2b4c6d8e0f1``): both carry a ``round_id`` column, which is exactly the
generic case that function already handles for "any table other than
workflow_rounds". They are frozen while their workflow is published, archived,
in review or approved — the same guarantee every other round-config table
already has.

WHAT THIS DOES NOT TOUCH
No column here is read by an agent, a copilot or an LLM client (candidate-
authored task content is exactly the prompt-injection surface D-03 deferred
portfolio rounds for). ``PanelVerdict.decision_authority`` stays structurally
``human_only`` — nothing in this migration gives a round a way to pass or fail
itself; ``post_round_review`` (a named human) is still the only writer of a
task round's outcome.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "a5d7f9b1c3e8"
down_revision: str | None = "f4c6e8a0b2d7"
branch_labels: str | None = None
depends_on: str | None = None

OLD_ROUND_KINDS = ("mcq", "coding", "ai_interview", "human_review")
ROUND_KINDS = (*OLD_ROUND_KINDS, "job_simulation", "portfolio")
TASK_KINDS = ("job_simulation", "portfolio")
TASK_RESPONSE_TYPES = ("text", "file", "link")
TASK_LINK_KINDS = ("repository", "design", "document", "video", "website", "other")
TASK_SUBMISSION_STATUSES = ("assigned", "in_progress", "submitted", "expired", "withdrawn")
TASK_CLOSED_BY = ("candidate", "time_limit")
TASK_CONTENT_TYPES = ("application/pdf", "image/jpeg", "image/png")
TASK_EVENT_ACTIONS = (
    "issued", "opened", "started", "saved", "artifact_added", "artifact_removed", "submitted",
    "closed_at_time_limit", "expired", "withdrawn", "reissued", "link_rotated",
    "artifact_downloaded", "submission_viewed", "round_task_updated",
)

NEW_ENROLMENT_AWAITS_HUMAN = """
CREATE OR REPLACE FUNCTION enrolment_awaits_human(p_status text, p_round_id uuid)
RETURNS boolean
LANGUAGE sql STABLE AS $$
    SELECT p_status NOT IN ('hired', 'rejected')
       AND (   p_status = 'held'
            OR (p_round_id IS NULL AND p_status = 'interviewed')
            OR EXISTS (SELECT 1 FROM workflow_rounds wr
                        WHERE wr.id = p_round_id
                          AND wr.kind IN ('human_review', 'job_simulation', 'portfolio')
                          AND wr.deleted_at IS NULL))
$$
"""

OLD_ENROLMENT_AWAITS_HUMAN = """
CREATE OR REPLACE FUNCTION enrolment_awaits_human(p_status text, p_round_id uuid)
RETURNS boolean
LANGUAGE sql STABLE AS $$
    SELECT p_status NOT IN ('hired', 'rejected')
       AND (   p_status = 'held'
            OR (p_round_id IS NULL AND p_status = 'interviewed')
            OR EXISTS (SELECT 1 FROM workflow_rounds wr
                        WHERE wr.id = p_round_id
                          AND wr.kind = 'human_review'
                          AND wr.deleted_at IS NULL))
$$
"""

# ---------------------------------------------------------------------------
# task_submissions lifecycle
# ---------------------------------------------------------------------------
TASK_SUBMISSIONS_LIFECYCLE = """
CREATE OR REPLACE FUNCTION task_submissions_lifecycle() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'task submission % is kept, not deleted', OLD.id;
    END IF;

    IF TG_OP = 'INSERT' THEN
        IF NEW.status <> 'assigned' THEN
            RAISE EXCEPTION 'a task submission arrives assigned';
        END IF;
        IF NEW.started_at IS NOT NULL OR NEW.submitted_at IS NOT NULL
           OR NEW.closed_by IS NOT NULL THEN
            RAISE EXCEPTION 'a task submission arrives with no progress recorded';
        END IF;
        IF NEW.superseded_at IS NOT NULL OR NEW.superseded_by_id IS NOT NULL THEN
            RAISE EXCEPTION 'a task submission arrives not superseded';
        END IF;
        IF NEW.redacted_at IS NOT NULL THEN
            RAISE EXCEPTION 'a task submission arrives not redacted';
        END IF;
        RETURN NEW;
    END IF;

    -- UPDATE: a redaction changes only redacted_at and token_hash (cleared),
    -- together, once, and nothing else in the same statement.
    IF NEW.redacted_at IS DISTINCT FROM OLD.redacted_at THEN
        IF OLD.redacted_at IS NOT NULL THEN
            RAISE EXCEPTION 'task submission % is already redacted', OLD.id;
        END IF;
        IF NEW.redacted_at IS NULL THEN
            RAISE EXCEPTION 'a task submission is never un-redacted';
        END IF;
        IF (to_jsonb(NEW) - 'redacted_at' - 'token_hash' - 'updated_at')
           IS DISTINCT FROM (to_jsonb(OLD) - 'redacted_at' - 'token_hash' - 'updated_at') THEN
            RAISE EXCEPTION 'a task submission redaction changes only redacted_at and token_hash';
        END IF;
        IF NEW.token_hash IS NOT NULL THEN
            RAISE EXCEPTION 'a redacted task submission has no live link';
        END IF;
        RETURN NEW;
    END IF;

    IF OLD.redacted_at IS NOT NULL THEN
        RAISE EXCEPTION 'task submission % is redacted and is fixed', OLD.id;
    END IF;

    -- Identity, the frozen configuration and the allowance snapshot are fixed
    -- once the candidate has started — nothing HR does afterwards should be
    -- able to move what a submission is being measured against.
    IF OLD.started_at IS NOT NULL THEN
        IF NEW.company_id IS DISTINCT FROM OLD.company_id
           OR NEW.enrolment_id IS DISTINCT FROM OLD.enrolment_id
           OR NEW.round_id IS DISTINCT FROM OLD.round_id
           OR NEW.applicant_id IS DISTINCT FROM OLD.applicant_id
           OR NEW.kind IS DISTINCT FROM OLD.kind
           OR NEW.config_digest IS DISTINCT FROM OLD.config_digest
           OR NEW.time_limit_seconds IS DISTINCT FROM OLD.time_limit_seconds
           OR NEW.base_time_limit_seconds IS DISTINCT FROM OLD.base_time_limit_seconds
           OR NEW.extra_time_seconds IS DISTINCT FROM OLD.extra_time_seconds
           OR NEW.deadline_extension_days IS DISTINCT FROM OLD.deadline_extension_days
           OR NEW.accommodation_id IS DISTINCT FROM OLD.accommodation_id THEN
            RAISE EXCEPTION
                'task submission % keeps its identity, config and allowance once started', OLD.id;
        END IF;
    END IF;

    -- due_at may only move LATER, and only while the submission is open — the
    -- shape a D2 deadline extension needs (app.accommodations.effective_for).
    IF NEW.due_at IS DISTINCT FROM OLD.due_at THEN
        IF OLD.status NOT IN ('assigned', 'in_progress') OR OLD.superseded_at IS NOT NULL THEN
            RAISE EXCEPTION 'task submission % is closed; its due date is fixed', OLD.id;
        END IF;
        IF NEW.due_at < OLD.due_at THEN
            RAISE EXCEPTION 'a task submission''s due date can only move later';
        END IF;
    END IF;

    IF NEW.status IS DISTINCT FROM OLD.status THEN
        IF OLD.status = 'assigned' AND NEW.status = 'in_progress' THEN
            IF NEW.started_at IS NULL THEN
                RAISE EXCEPTION 'a task submission starting needs started_at';
            END IF;
        ELSIF OLD.status IN ('assigned', 'in_progress') AND NEW.status = 'submitted' THEN
            IF NEW.submitted_at IS NULL OR NEW.closed_by IS NULL THEN
                RAISE EXCEPTION 'a submitted task submission needs submitted_at and closed_by';
            END IF;
        ELSIF OLD.status IN ('assigned', 'in_progress') AND NEW.status IN ('expired', 'withdrawn')
        THEN
            NULL;  -- no further fields required
        ELSE
            RAISE EXCEPTION 'task submission % cannot go from % to %', OLD.id, OLD.status, NEW.status;
        END IF;
    ELSIF OLD.status IN ('submitted', 'expired', 'withdrawn') THEN
        -- Terminal: frozen except the once-only supersede pair and the link
        -- being cleared (never replaced).
        IF (to_jsonb(NEW) - 'superseded_at' - 'superseded_by_id' - 'token_hash' - 'updated_at')
           IS DISTINCT FROM
           (to_jsonb(OLD) - 'superseded_at' - 'superseded_by_id' - 'token_hash' - 'updated_at')
        THEN
            RAISE EXCEPTION 'task submission % is %; it is fixed', OLD.id, OLD.status;
        END IF;
        IF NEW.token_hash IS NOT NULL AND NEW.token_hash IS DISTINCT FROM OLD.token_hash THEN
            RAISE EXCEPTION 'a closed task submission''s link is only ever cleared, not replaced';
        END IF;
    END IF;

    IF OLD.superseded_by_id IS NOT NULL THEN
        IF NEW.superseded_by_id IS DISTINCT FROM OLD.superseded_by_id
           OR NEW.superseded_at IS DISTINCT FROM OLD.superseded_at THEN
            RAISE EXCEPTION 'task submission % keeps what superseded it', OLD.id;
        END IF;
    ELSIF (NEW.superseded_by_id IS NULL) <> (NEW.superseded_at IS NULL) THEN
        RAISE EXCEPTION
            'task submission % must record what and when it was superseded together', OLD.id;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""

TASK_RESPONSES_FROZEN = """
CREATE OR REPLACE FUNCTION task_responses_frozen() RETURNS trigger AS $$
DECLARE
    sub_status text;
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF EXISTS (SELECT 1 FROM task_submissions s WHERE s.id = OLD.submission_id) THEN
            RAISE EXCEPTION 'task response % is kept, not deleted', OLD.id;
        END IF;
        RETURN OLD;  -- cascade: the submission (or its enrolment) is gone
    END IF;

    SELECT s.status INTO sub_status FROM task_submissions s
     WHERE s.id = COALESCE(NEW.submission_id, OLD.submission_id);
    IF sub_status IS NULL THEN
        RETURN COALESCE(NEW, OLD);  -- the parent is already gone: a cascade
    END IF;

    IF TG_OP = 'INSERT' THEN
        IF sub_status NOT IN ('assigned', 'in_progress') THEN
            RAISE EXCEPTION 'task submission is %; responses can no longer be added', sub_status;
        END IF;
        IF NEW.redacted_at IS NOT NULL THEN
            RAISE EXCEPTION 'a task response arrives not redacted';
        END IF;
        RETURN NEW;
    END IF;

    -- UPDATE: a redaction clears the content, once, and nothing else moves.
    IF NEW.redacted_at IS DISTINCT FROM OLD.redacted_at THEN
        IF OLD.redacted_at IS NOT NULL THEN
            RAISE EXCEPTION 'task response % is already redacted', OLD.id;
        END IF;
        IF NEW.redacted_at IS NULL THEN
            RAISE EXCEPTION 'a task response is never un-redacted';
        END IF;
        IF NEW.text_value IS NOT NULL OR NEW.link_url IS NOT NULL OR NEW.title IS NOT NULL
           OR NEW.description IS NOT NULL OR NEW.storage_key IS NOT NULL
           OR NEW.original_name IS NOT NULL THEN
            RAISE EXCEPTION 'a redacted task response clears its content';
        END IF;
        RETURN NEW;
    END IF;

    IF OLD.redacted_at IS NOT NULL THEN
        RAISE EXCEPTION 'task response % is redacted and is fixed', OLD.id;
    END IF;

    IF sub_status NOT IN ('assigned', 'in_progress') THEN
        RAISE EXCEPTION 'task submission is %; its responses are fixed', sub_status;
    END IF;

    -- File fields never change once set: a replacement is a new row.
    IF OLD.storage_key IS NOT NULL AND NEW.storage_key IS DISTINCT FROM OLD.storage_key THEN
        RAISE EXCEPTION 'a task response''s file never changes; add a new one instead';
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""

TASK_EVENTS_APPEND_ONLY = """
CREATE OR REPLACE FUNCTION task_events_append_only() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF (OLD.submission_id IS NULL
            OR NOT EXISTS (SELECT 1 FROM task_submissions s WHERE s.id = OLD.submission_id))
           AND (OLD.round_id IS NULL
                OR NOT EXISTS (SELECT 1 FROM workflow_rounds wr WHERE wr.id = OLD.round_id)) THEN
            RETURN OLD;  -- cascade: what this event was about is gone
        END IF;
        RAISE EXCEPTION 'task_events is append-only';
    END IF;
    IF TG_OP = 'UPDATE' AND NEW.actor_user_id IS NULL AND OLD.actor_user_id IS NOT NULL
       AND (to_jsonb(NEW) - 'actor_user_id') = (to_jsonb(OLD) - 'actor_user_id') THEN
        RETURN NEW;  -- a deleted user's id being cleared (ON DELETE SET NULL)
    END IF;
    RAISE EXCEPTION 'task_events is append-only';
END;
$$ LANGUAGE plpgsql;
"""


def _ts(name: str, nullable: bool = True) -> sa.Column:
    return sa.Column(name, sa.TIMESTAMP(timezone=True), nullable=nullable)


def upgrade() -> None:
    # -----------------------------------------------------------------
    # The two new round kinds
    # -----------------------------------------------------------------
    op.drop_constraint("ck_workflow_rounds_kind", "workflow_rounds", type_="check")
    op.create_check_constraint(
        "ck_workflow_rounds_kind", "workflow_rounds", f"kind IN {ROUND_KINDS}",
    )
    op.execute(NEW_ENROLMENT_AWAITS_HUMAN)

    # -----------------------------------------------------------------
    # round_tasks — HR's configuration, one row per round
    # -----------------------------------------------------------------
    op.create_table(
        "round_tasks",
        sa.Column("id", sa.Uuid(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("round_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("brief", sa.Text(), nullable=False),
        sa.Column("brief_translations", postgresql.JSONB(), nullable=True),
        sa.Column("items", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("min_artifacts", sa.SmallInteger(), nullable=True),
        sa.Column("max_artifacts", sa.SmallInteger(), nullable=True),
        sa.Column("allow_files", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("allow_links", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("allowed_link_domains", postgresql.ARRAY(sa.Text()), nullable=True),
        _ts("created_at", nullable=False), _ts("updated_at", nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_round_tasks"),
        sa.UniqueConstraint("round_id", name="uq_round_tasks_round"),
        sa.UniqueConstraint("id", "company_id", name="uq_round_tasks_id_company"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"],
                                name="fk_round_tasks_company", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["round_id", "company_id"], ["workflow_rounds.id", "workflow_rounds.company_id"],
            name="fk_round_tasks_round", ondelete="CASCADE",
        ),
        sa.CheckConstraint(f"kind IN {TASK_KINDS}", name="ck_round_tasks_kind"),
        sa.CheckConstraint("char_length(brief) BETWEEN 1 AND 20000", name="ck_round_tasks_brief_len"),
        # No subquery in a CHECK (Postgres refuses it): remove the two allowed
        # keys and require nothing is left, rather than scanning jsonb_object_keys.
        sa.CheckConstraint(
            "brief_translations IS NULL OR (brief_translations - 'hi' - 'te') = '{}'::jsonb",
            name="ck_round_tasks_brief_translations_keys",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(items) = 'array' AND jsonb_array_length(items) <= 20",
            name="ck_round_tasks_items_shape",
        ),
        sa.CheckConstraint(
            "min_artifacts IS NULL OR (max_artifacts IS NOT NULL AND min_artifacts >= 0"
            " AND max_artifacts >= min_artifacts AND max_artifacts <= 20)",
            name="ck_round_tasks_artifact_range",
        ),
    )
    op.alter_column("round_tasks", "created_at", server_default=sa.text("now()"))
    op.alter_column("round_tasks", "updated_at", server_default=sa.text("now()"))

    # -----------------------------------------------------------------
    # round_task_materials — HR's reference attachments
    # -----------------------------------------------------------------
    op.create_table(
        "round_task_materials",
        sa.Column("id", sa.Uuid(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("round_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        # Deliberately NOT unique: cloning a workflow (workflows.clone_for_edit)
        # copies a material row and SHARES the original's storage key rather
        # than duplicating the bytes (open decision 21) — HR reference
        # material is immutable and identical across versions. Removing an
        # object therefore has to check no other row still names the key
        # (app/job_tasks.py::remove_material does).
        sa.Column("storage_key", sa.Text(), nullable=False),
        sa.Column("original_name", sa.Text(), nullable=True),
        sa.Column("content_type", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.Text(), nullable=False),
        sa.Column("position", sa.SmallInteger(), nullable=False, server_default="0"),
        _ts("created_at", nullable=False), _ts("updated_at", nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_round_task_materials"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"],
                                name="fk_round_task_materials_company", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["round_id", "company_id"], ["workflow_rounds.id", "workflow_rounds.company_id"],
            name="fk_round_task_materials_round", ondelete="CASCADE",
        ),
        sa.CheckConstraint("char_length(title) BETWEEN 1 AND 200",
                           name="ck_round_task_materials_title_len"),
        sa.CheckConstraint(f"content_type IN {TASK_CONTENT_TYPES}",
                           name="ck_round_task_materials_content_type"),
        sa.CheckConstraint("size_bytes BETWEEN 1 AND 10485760", name="ck_round_task_materials_size"),
        sa.CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name="ck_round_task_materials_sha256"),
        sa.CheckConstraint("original_name IS NULL OR char_length(original_name) <= 200",
                           name="ck_round_task_materials_name"),
    )
    op.alter_column("round_task_materials", "created_at", server_default=sa.text("now()"))
    op.alter_column("round_task_materials", "updated_at", server_default=sa.text("now()"))
    op.create_index("ix_round_task_materials_round", "round_task_materials",
                    ["round_id", "position"])
    op.create_index("ix_round_task_materials_storage_key", "round_task_materials",
                    ["storage_key"])

    # -----------------------------------------------------------------
    # task_submissions — the lifecycle row, one live per (enrolment, round)
    # -----------------------------------------------------------------
    op.create_table(
        "task_submissions",
        sa.Column("id", sa.Uuid(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("enrolment_id", sa.Uuid(), nullable=False),
        sa.Column("round_id", sa.Uuid(), nullable=False),
        sa.Column("applicant_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="assigned"),
        sa.Column("token_hash", sa.Text(), nullable=True),
        sa.Column("due_at", sa.TIMESTAMP(timezone=True), nullable=False),
        _ts("started_at"), _ts("submitted_at"),
        sa.Column("closed_by", sa.Text(), nullable=True),
        sa.Column("time_limit_seconds", sa.Integer(), nullable=True),
        sa.Column("base_time_limit_seconds", sa.Integer(), nullable=True),
        sa.Column("extra_time_seconds", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("deadline_extension_days", sa.SmallInteger(), nullable=True),
        sa.Column("accommodation_id", sa.Uuid(), nullable=True),
        sa.Column("config_digest", sa.Text(), nullable=False),
        sa.Column("attempt_no", sa.SmallInteger(), nullable=False, server_default="1"),
        _ts("superseded_at"),
        sa.Column("superseded_by_id", sa.Uuid(), nullable=True),
        sa.Column("issued_by_user_id", sa.Uuid(), nullable=True),
        _ts("consented_at"), _ts("redacted_at"),
        _ts("created_at", nullable=False), _ts("updated_at", nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_task_submissions"),
        sa.UniqueConstraint("id", "company_id", name="uq_task_submissions_id_company"),
        sa.UniqueConstraint("token_hash", name="uq_task_submissions_token_hash"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"],
                                name="fk_task_submissions_company", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["enrolment_id", "company_id"], ["enrolments.id", "enrolments.company_id"],
            name="fk_task_submissions_enrolment", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["round_id", "company_id"], ["workflow_rounds.id", "workflow_rounds.company_id"],
            name="fk_task_submissions_round", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["applicant_id", "company_id"], ["applicants.id", "applicants.company_id"],
            name="fk_task_submissions_applicant", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["accommodation_id", "company_id"],
            ["candidate_accommodations.id", "candidate_accommodations.company_id"],
            name="fk_task_submissions_accommodation", ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(["issued_by_user_id"], ["users.id"],
                                name="fk_task_submissions_issued_by", ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["superseded_by_id", "company_id"],
            ["task_submissions.id", "task_submissions.company_id"],
            name="fk_task_submissions_superseded_by", ondelete="SET NULL",
            deferrable=True, initially="DEFERRED",
        ),
        sa.CheckConstraint(f"kind IN {TASK_KINDS}", name="ck_task_submissions_kind"),
        sa.CheckConstraint(f"status IN {TASK_SUBMISSION_STATUSES}",
                           name="ck_task_submissions_status"),
        sa.CheckConstraint(f"closed_by IS NULL OR closed_by IN {TASK_CLOSED_BY}",
                           name="ck_task_submissions_closed_by"),
        sa.CheckConstraint("config_digest ~ '^[0-9a-f]{64}$'", name="ck_task_submissions_digest"),
        sa.CheckConstraint("attempt_no >= 1", name="ck_task_submissions_attempt_no"),
        sa.CheckConstraint("extra_time_seconds >= 0", name="ck_task_submissions_extra_time"),
    )
    op.alter_column("task_submissions", "created_at", server_default=sa.text("now()"))
    op.alter_column("task_submissions", "updated_at", server_default=sa.text("now()"))
    op.create_index(
        "uq_task_submissions_live_per_round", "task_submissions", ["enrolment_id", "round_id"],
        unique=True, postgresql_where=sa.text("superseded_at IS NULL"),
    )
    op.create_index("ix_task_submissions_applicant", "task_submissions",
                    ["company_id", "applicant_id"])
    op.create_index(
        "ix_task_submissions_due", "task_submissions", ["due_at"],
        postgresql_where=sa.text("status IN ('assigned', 'in_progress')"),
    )

    # -----------------------------------------------------------------
    # task_responses — what the candidate actually submitted
    # -----------------------------------------------------------------
    op.create_table(
        "task_responses",
        sa.Column("id", sa.Uuid(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("submission_id", sa.Uuid(), nullable=False),
        sa.Column("item_key", sa.Text(), nullable=True),
        sa.Column("position", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column("response_type", sa.Text(), nullable=False),
        sa.Column("text_value", sa.Text(), nullable=True),
        sa.Column("link_url", sa.Text(), nullable=True),
        sa.Column("link_kind", sa.Text(), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("storage_key", sa.Text(), nullable=True),
        sa.Column("original_name", sa.Text(), nullable=True),
        sa.Column("content_type", sa.Text(), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column("sha256", sa.Text(), nullable=True),
        _ts("created_at", nullable=False), _ts("updated_at", nullable=False), _ts("redacted_at"),
        sa.PrimaryKeyConstraint("id", name="pk_task_responses"),
        sa.UniqueConstraint("storage_key", name="uq_task_responses_storage_key"),
        sa.ForeignKeyConstraint(
            ["submission_id", "company_id"], ["task_submissions.id", "task_submissions.company_id"],
            name="fk_task_responses_submission", ondelete="CASCADE",
        ),
        sa.CheckConstraint(f"response_type IN {TASK_RESPONSE_TYPES}",
                           name="ck_task_responses_type"),
        sa.CheckConstraint(f"link_kind IS NULL OR link_kind IN {TASK_LINK_KINDS}",
                           name="ck_task_responses_link_kind"),
        sa.CheckConstraint("item_key IS NULL OR char_length(item_key) <= 80",
                           name="ck_task_responses_item_key_len"),
        sa.CheckConstraint("text_value IS NULL OR char_length(text_value) <= 20000",
                           name="ck_task_responses_text_len"),
        sa.CheckConstraint("link_url IS NULL OR char_length(link_url) <= 2000",
                           name="ck_task_responses_link_len"),
        sa.CheckConstraint("link_url IS NULL OR link_url LIKE 'https://%'",
                           name="ck_task_responses_link_https"),
        sa.CheckConstraint("title IS NULL OR char_length(title) <= 200",
                           name="ck_task_responses_title_len"),
        sa.CheckConstraint("description IS NULL OR char_length(description) <= 2000",
                           name="ck_task_responses_description_len"),
        sa.CheckConstraint("content_type IS NULL OR content_type IN " + str(TASK_CONTENT_TYPES),
                           name="ck_task_responses_content_type"),
        sa.CheckConstraint(
            "redacted_at IS NOT NULL OR"
            " (response_type = 'text' AND text_value IS NOT NULL"
            "   AND storage_key IS NULL AND link_url IS NULL)"
            " OR (response_type = 'file' AND storage_key IS NOT NULL"
            "   AND text_value IS NULL AND link_url IS NULL)"
            " OR (response_type = 'link' AND link_url IS NOT NULL"
            "   AND storage_key IS NULL AND text_value IS NULL)",
            name="ck_task_responses_shape",
        ),
    )
    op.alter_column("task_responses", "created_at", server_default=sa.text("now()"))
    op.alter_column("task_responses", "updated_at", server_default=sa.text("now()"))
    op.create_index(
        "uq_task_responses_item", "task_responses", ["submission_id", "item_key"],
        unique=True, postgresql_where=sa.text("item_key IS NOT NULL"),
    )
    op.create_index("ix_task_responses_submission", "task_responses",
                    ["submission_id", "position"])

    # -----------------------------------------------------------------
    # task_events — append-only history
    # -----------------------------------------------------------------
    op.create_table(
        "task_events",
        sa.Column("id", sa.Uuid(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("submission_id", sa.Uuid(), nullable=True),
        sa.Column("round_id", sa.Uuid(), nullable=True),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("actor_type", sa.Text(), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), nullable=True),
        sa.Column("details", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        _ts("created_at", nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_task_events"),
        sa.ForeignKeyConstraint(
            ["submission_id", "company_id"], ["task_submissions.id", "task_submissions.company_id"],
            name="fk_task_events_submission", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["round_id", "company_id"], ["workflow_rounds.id", "workflow_rounds.company_id"],
            name="fk_task_events_round", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"],
                                name="fk_task_events_actor", ondelete="SET NULL"),
        sa.CheckConstraint(f"action IN {TASK_EVENT_ACTIONS}", name="ck_task_events_action"),
        sa.CheckConstraint("actor_type IN ('candidate', 'user', 'system')",
                           name="ck_task_events_actor_type"),
        sa.CheckConstraint("submission_id IS NOT NULL OR round_id IS NOT NULL",
                           name="ck_task_events_target"),
    )
    op.alter_column("task_events", "created_at", server_default=sa.text("now()"))
    op.create_index("ix_task_events_submission", "task_events", ["submission_id", "created_at"])
    op.create_index("ix_task_events_round", "task_events", ["round_id", "created_at"])

    # -----------------------------------------------------------------
    # Triggers
    # -----------------------------------------------------------------
    for sql in (TASK_SUBMISSIONS_LIFECYCLE, TASK_RESPONSES_FROZEN, TASK_EVENTS_APPEND_ONLY):
        op.execute(sql)
    op.execute(
        "CREATE TRIGGER task_submissions_lifecycle"
        " BEFORE INSERT OR UPDATE OR DELETE ON task_submissions"
        " FOR EACH ROW EXECUTE FUNCTION task_submissions_lifecycle()"
    )
    op.execute(
        "CREATE TRIGGER task_responses_frozen"
        " BEFORE INSERT OR UPDATE OR DELETE ON task_responses"
        " FOR EACH ROW EXECUTE FUNCTION task_responses_frozen()"
    )
    op.execute(
        "CREATE TRIGGER task_events_append_only"
        " BEFORE UPDATE OR DELETE ON task_events"
        " FOR EACH ROW EXECUTE FUNCTION task_events_append_only()"
    )
    # round_tasks / round_task_materials: the EXISTING trigger (a2b4c6d8e0f1)
    # already handles any table with a round_id column other than
    # workflow_rounds itself — reused unchanged.
    for table in ("round_tasks", "round_task_materials"):
        op.execute(
            f"CREATE TRIGGER {table}_no_edit_when_published"
            f" BEFORE INSERT OR UPDATE OR DELETE ON {table}"
            " FOR EACH ROW EXECUTE FUNCTION workflow_children_immutable()"
        )


def downgrade() -> None:
    conn = op.get_bind()
    stuck = conn.execute(
        sa.text(f"SELECT count(*) FROM workflow_rounds WHERE kind IN {TASK_KINDS}")
    ).scalar()
    if stuck:
        raise RuntimeError(
            f"{stuck} workflow_rounds still use a PH4-D4 round kind "
            "(job_simulation/portfolio); remove or re-kind them before downgrading."
        )

    for table in ("round_tasks", "round_task_materials"):
        op.execute(f"DROP TRIGGER IF EXISTS {table}_no_edit_when_published ON {table}")
    op.execute("DROP TRIGGER IF EXISTS task_events_append_only ON task_events")
    op.execute("DROP FUNCTION IF EXISTS task_events_append_only()")
    op.execute("DROP TRIGGER IF EXISTS task_responses_frozen ON task_responses")
    op.execute("DROP FUNCTION IF EXISTS task_responses_frozen()")
    op.execute("DROP TRIGGER IF EXISTS task_submissions_lifecycle ON task_submissions")
    op.execute("DROP FUNCTION IF EXISTS task_submissions_lifecycle()")

    op.drop_index("ix_task_events_round", table_name="task_events")
    op.drop_index("ix_task_events_submission", table_name="task_events")
    op.drop_table("task_events")

    op.drop_index("ix_task_responses_submission", table_name="task_responses")
    op.drop_index("uq_task_responses_item", table_name="task_responses")
    op.drop_table("task_responses")

    op.drop_index("ix_task_submissions_due", table_name="task_submissions")
    op.drop_index("ix_task_submissions_applicant", table_name="task_submissions")
    op.drop_index("uq_task_submissions_live_per_round", table_name="task_submissions")
    op.drop_table("task_submissions")

    op.drop_index("ix_round_task_materials_storage_key", table_name="round_task_materials")
    op.drop_index("ix_round_task_materials_round", table_name="round_task_materials")
    op.drop_table("round_task_materials")

    op.drop_table("round_tasks")

    op.execute(OLD_ENROLMENT_AWAITS_HUMAN)
    op.drop_constraint("ck_workflow_rounds_kind", "workflow_rounds", type_="check")
    op.create_check_constraint(
        "ck_workflow_rounds_kind", "workflow_rounds", f"kind IN {OLD_ROUND_KINDS}",
    )
