"""Workflow versions are reviewed and approved before they go live — PH4-O6.

Revision ID: a2b4c6d8e0f1
Revises: f4b6d8e0a2c3
Create Date: 2026-09-18

THE LIFECYCLE (per version, decision D4-2: the company super admin approves)

    draft ──submit──▶ in_review ──approve──▶ approved ──publish──▶ published
      ▲                  │   │                  │
      │                  │   └─request changes─▶ changes_requested ──resubmit──┐
      │                  └───────withdraw──────┐                               │
      └──────────────────────reopen────────────┴───────────────────────────────┘

``review_status`` is a second axis beside ``status`` (draft/published/archived),
the same split the requisition approval made in PH3-B2: ``status`` is where the
version is in its life, ``review_status`` is whether a second person has agreed
to it. Every published or archived version that exists today was live before
this story; they are recorded as ``approved`` (added with that default, so no
row trigger fires), and every existing draft as ``draft``.

WHERE THE GATE HOLDS
The application refuses to publish anything not approved, and says why. The
database enforces the whole lifecycle too, so no writer — the API, a script, an
importer — can reach 'published' without two people having been through it:

* an INSERT must be a draft in review state 'draft' (``create_draft`` is the
  only inserter, and cloning goes through it);
* ``in_review`` only from draft or changes_requested, with a submitter and a
  content fingerprint recorded — and the submitter changes at no other time;
* ``approved`` only from ``in_review``, with a reviewer who is neither the
  submitter nor the version's author, and the fingerprint unchanged;
* ``changes_requested`` only from ``in_review``, with a reviewer who is not the
  submitter;
* 'published' only from a draft that was ALREADY approved before this update
  (``OLD.review_status``), so approving and publishing in one statement is
  refused;
* an archived version never comes back, and its content is frozen like a
  published one's — candidates may still be running it.

The security review found the first version of this trigger checked only the
last step (``NEW.review_status``), on UPDATE only; these rules replace it.

WHAT IS LOCKED WHILE UNDER REVIEW
A version that is ``in_review`` or ``approved`` cannot be edited: its settings,
rounds and criteria are exactly what the reviewer saw. The row may still change
its review fields, move to 'published', and be soft-deleted. To change an
approved version it is reopened, which returns it to ``draft`` and clears the
approval — approving one thing and publishing another is the failure this
exists to prevent. Interview kits (PH4-A5) and stage owners/SLAs (PH4-O1) are
operational guidance kept in their own tables, so they stay editable.

THE HISTORY
``workflow_review_events`` is append-only: every submission, withdrawal,
approval, request for changes and reopening, with who, when and the note. The
columns on ``workflows`` say where a version stands now; the events say how it
got there.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "a2b4c6d8e0f1"
down_revision: str | None = "f4b6d8e0a2c3"
branch_labels: str | None = None
depends_on: str | None = None

REVIEW_STATES = ("draft", "in_review", "changes_requested", "approved")
REVIEW_ACTIONS = ("submitted", "withdrawn", "approved", "changes_requested", "reopened")

WORKFLOW_TRIGGER = """
CREATE OR REPLACE FUNCTION workflows_published_immutable() RETURNS trigger AS $$
DECLARE
    -- Columns that may change while a draft is under review or approved: the
    -- review record itself, and publishing.
    review_keys text[] := ARRAY['review_status', 'submitted_for_review_at',
                                'submitted_by_user_id', 'reviewed_at',
                                'reviewed_by_user_id', 'review_note',
                                'review_fingerprint', 'status', 'published_at',
                                'updated_at', 'deleted_at'];
BEGIN
    IF TG_OP = 'INSERT' THEN
        -- PH4-O6: every version starts life as an unreviewed draft.
        IF NEW.status IS DISTINCT FROM 'draft' OR NEW.review_status IS DISTINCT FROM 'draft' THEN
            RAISE EXCEPTION
                'workflow % must be created as an unreviewed draft (got status %, review %)',
                NEW.id, NEW.status, NEW.review_status;
        END IF;
        RETURN NEW;
    END IF;
    IF TG_OP = 'DELETE' THEN
        IF OLD.status = 'published'
           AND EXISTS (SELECT 1 FROM job_requisitions r WHERE r.id = OLD.requisition_id) THEN
            RAISE EXCEPTION
                'workflow % is published and cannot be deleted; archive it instead', OLD.id;
        END IF;
        RETURN OLD;
    END IF;

    -- PH4-O6: only a version approved BEFORE this update goes live.
    IF NEW.status = 'published' AND OLD.status IS DISTINCT FROM 'published' THEN
        IF OLD.status IS DISTINCT FROM 'draft' THEN
            RAISE EXCEPTION
                'workflow % is %; only a draft can be published', OLD.id, OLD.status;
        END IF;
        IF OLD.review_status IS DISTINCT FROM 'approved'
           OR NEW.review_status IS DISTINCT FROM 'approved' THEN
            RAISE EXCEPTION
                'workflow % has not been approved (review status %); submit it for review first',
                OLD.id, OLD.review_status;
        END IF;
    END IF;

    -- PH4-O6: who submitted is fixed at submission. Were it writable later, one
    -- statement could approve as A while renaming the submitter A → B.
    IF NEW.submitted_by_user_id IS DISTINCT FROM OLD.submitted_by_user_id
       AND NOT (NEW.review_status = 'in_review'
                AND OLD.review_status IS DISTINCT FROM 'in_review') THEN
        RAISE EXCEPTION
            'workflow %: the submitter is recorded on submission and cannot be changed', OLD.id;
    END IF;

    -- PH4-O6: the review lifecycle, step by step.
    IF NEW.review_status IS DISTINCT FROM OLD.review_status THEN
        IF OLD.status IS DISTINCT FROM 'draft' THEN
            RAISE EXCEPTION 'workflow % is %; its review is closed', OLD.id, OLD.status;
        END IF;
        IF NEW.review_status = 'in_review' THEN
            IF OLD.review_status NOT IN ('draft', 'changes_requested')
               OR NEW.submitted_by_user_id IS NULL OR NEW.review_fingerprint IS NULL THEN
                RAISE EXCEPTION
                    'workflow % cannot be submitted for review from % without a submitter '
                    'and a fingerprint', OLD.id, OLD.review_status;
            END IF;
        ELSIF NEW.review_status = 'approved' THEN
            IF OLD.review_status IS DISTINCT FROM 'in_review'
               OR OLD.submitted_by_user_id IS NULL
               OR NEW.reviewed_by_user_id IS NULL
               OR NEW.reviewed_by_user_id IS NOT DISTINCT FROM OLD.submitted_by_user_id
               OR NEW.reviewed_by_user_id IS NOT DISTINCT FROM OLD.created_by_user_id
               OR NEW.review_fingerprint IS NULL
               OR NEW.review_fingerprint IS DISTINCT FROM OLD.review_fingerprint THEN
                RAISE EXCEPTION
                    'workflow % cannot be approved: only a version in review, by someone '
                    'other than its author and submitter, unchanged since submission', OLD.id;
            END IF;
        ELSIF NEW.review_status = 'changes_requested' THEN
            IF OLD.review_status IS DISTINCT FROM 'in_review'
               OR NEW.reviewed_by_user_id IS NULL
               OR NEW.reviewed_by_user_id IS NOT DISTINCT FROM OLD.submitted_by_user_id THEN
                RAISE EXCEPTION
                    'workflow % cannot be sent back: only a version in review, by someone '
                    'other than its submitter', OLD.id;
            END IF;
        END IF;
    END IF;

    IF OLD.status = 'published' THEN
        IF NEW.status NOT IN ('published', 'archived') THEN
            RAISE EXCEPTION
                'workflow % is published; create a new version instead of reopening it', OLD.id;
        END IF;
        IF (to_jsonb(NEW) - 'status' - 'published_at' - 'updated_at' - 'deleted_at')
           IS DISTINCT FROM
           (to_jsonb(OLD) - 'status' - 'published_at' - 'updated_at' - 'deleted_at') THEN
            RAISE EXCEPTION
                'workflow % is published and cannot be edited; candidates already enrolled '
                'must finish on the version they started (create version n+1)', OLD.id;
        END IF;
    ELSIF OLD.status = 'archived' THEN
        -- Candidates may still be running an archived version: it is as
        -- frozen as a live one, and it never comes back.
        IF NEW.status IS DISTINCT FROM 'archived'
           OR (to_jsonb(NEW) - 'updated_at' - 'deleted_at')
              IS DISTINCT FROM (to_jsonb(OLD) - 'updated_at' - 'deleted_at') THEN
            RAISE EXCEPTION
                'workflow % is archived and cannot change; candidates may still be running it',
                OLD.id;
        END IF;
    ELSIF OLD.review_status IN ('in_review', 'approved') THEN
        -- PH4-O6: what the reviewer saw is what goes live.
        IF (to_jsonb(NEW) - review_keys) IS DISTINCT FROM (to_jsonb(OLD) - review_keys) THEN
            RAISE EXCEPTION
                'workflow % is % and cannot be edited; withdraw or reopen it first',
                OLD.id, OLD.review_status;
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""

CHILDREN_TRIGGER = """
CREATE OR REPLACE FUNCTION workflow_children_immutable() RETURNS trigger AS $$
DECLARE
    wf_id uuid;
    wf_status text;
    wf_review text;
BEGIN
    IF TG_TABLE_NAME = 'workflow_rounds' THEN
        wf_id := COALESCE(NEW.workflow_id, OLD.workflow_id);
    ELSE
        SELECT wr.workflow_id INTO wf_id FROM workflow_rounds wr
         WHERE wr.id = COALESCE(NEW.round_id, OLD.round_id);
    END IF;
    SELECT w.status, w.review_status INTO wf_status, wf_review
      FROM workflows w WHERE w.id = wf_id;
    -- No workflow (or no round): the parent is already gone, so this is the
    -- cascade cleaning up after it.
    IF wf_status IS NULL THEN
        RETURN COALESCE(NEW, OLD);
    END IF;
    IF wf_status IN ('published', 'archived') THEN
        RAISE EXCEPTION
            'workflow % is %; its rounds and criteria cannot change '
            '(create version n+1)', wf_id, wf_status;
    END IF;
    IF wf_review IN ('in_review', 'approved') THEN
        RAISE EXCEPTION
            'workflow % is %; its rounds and criteria cannot change until it is '
            'withdrawn or reopened', wf_id, wf_review;
    END IF;
    RETURN COALESCE(NEW, OLD);
END;
$$ LANGUAGE plpgsql;
"""

# The pre-O6 bodies, restored on downgrade.
OLD_WORKFLOW_TRIGGER = """
CREATE OR REPLACE FUNCTION workflows_published_immutable() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF OLD.status = 'published'
           AND EXISTS (SELECT 1 FROM job_requisitions r WHERE r.id = OLD.requisition_id) THEN
            RAISE EXCEPTION
                'workflow % is published and cannot be deleted; archive it instead', OLD.id;
        END IF;
        RETURN OLD;
    END IF;
    IF OLD.status = 'published' THEN
        IF NEW.status NOT IN ('published', 'archived') THEN
            RAISE EXCEPTION
                'workflow % is published; create a new version instead of reopening it', OLD.id;
        END IF;
        IF (to_jsonb(NEW) - 'status' - 'published_at' - 'updated_at' - 'deleted_at')
           IS DISTINCT FROM
           (to_jsonb(OLD) - 'status' - 'published_at' - 'updated_at' - 'deleted_at') THEN
            RAISE EXCEPTION
                'workflow % is published and cannot be edited; candidates already enrolled '
                'must finish on the version they started (create version n+1)', OLD.id;
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""

OLD_CHILDREN_TRIGGER = """
CREATE OR REPLACE FUNCTION workflow_children_immutable() RETURNS trigger AS $$
DECLARE
    wf_id uuid;
    wf_status text;
BEGIN
    IF TG_TABLE_NAME = 'workflow_rounds' THEN
        wf_id := COALESCE(NEW.workflow_id, OLD.workflow_id);
    ELSE
        SELECT wr.workflow_id INTO wf_id FROM workflow_rounds wr
         WHERE wr.id = COALESCE(NEW.round_id, OLD.round_id);
    END IF;
    SELECT w.status INTO wf_status FROM workflows w WHERE w.id = wf_id;
    IF wf_status IS NULL THEN
        RETURN COALESCE(NEW, OLD);
    END IF;
    IF wf_status = 'published' THEN
        RAISE EXCEPTION
            'workflow % is published; its rounds and criteria cannot change '
            '(create version n+1)', wf_id;
    END IF;
    RETURN COALESCE(NEW, OLD);
END;
$$ LANGUAGE plpgsql;
"""

EVENTS_TRIGGER = """
CREATE OR REPLACE FUNCTION workflow_review_events_append_only() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE'
       AND NOT EXISTS (SELECT 1 FROM workflows w WHERE w.id = OLD.workflow_id) THEN
        RETURN OLD;  -- the workflow went; its history goes with it
    END IF;
    IF TG_OP = 'UPDATE'
       AND NEW.actor_user_id IS NULL AND OLD.actor_user_id IS NOT NULL
       AND (to_jsonb(NEW) - 'actor_user_id') = (to_jsonb(OLD) - 'actor_user_id') THEN
        RETURN NEW;  -- a deleted user's id being cleared (ON DELETE SET NULL)
    END IF;
    RAISE EXCEPTION 'workflow_review_events is append-only: % is not permitted', TG_OP;
END;
$$ LANGUAGE plpgsql;
"""


def upgrade() -> None:
    # Added as 'approved' so every existing row — all of them live before this
    # story — is recorded as such without an UPDATE (which the published-row
    # trigger would refuse). Drafts are then set back, and the default moves.
    op.add_column(
        "workflows",
        sa.Column("review_status", sa.Text(), nullable=False, server_default="approved"),
    )
    op.execute("UPDATE workflows SET review_status = 'draft' WHERE status = 'draft'")
    op.alter_column("workflows", "review_status", server_default="draft")
    op.add_column(
        "workflows", sa.Column("submitted_for_review_at", sa.TIMESTAMP(timezone=True))
    )
    op.add_column("workflows", sa.Column("submitted_by_user_id", sa.Uuid()))
    op.add_column("workflows", sa.Column("reviewed_at", sa.TIMESTAMP(timezone=True)))
    op.add_column("workflows", sa.Column("reviewed_by_user_id", sa.Uuid()))
    op.add_column("workflows", sa.Column("review_note", sa.Text()))
    # A hash of the version's content at submission: the approval is of THIS.
    op.add_column("workflows", sa.Column("review_fingerprint", sa.Text()))
    op.create_foreign_key(
        "fk_workflows_submitted_by", "workflows", "users",
        ["submitted_by_user_id"], ["id"], ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_workflows_reviewed_by", "workflows", "users",
        ["reviewed_by_user_id"], ["id"], ondelete="SET NULL",
    )
    op.create_check_constraint(
        "ck_workflows_review_status", "workflows",
        f"review_status IN {REVIEW_STATES}",
    )
    op.create_check_constraint(
        "ck_workflows_review_note_len", "workflows",
        "review_note IS NULL OR char_length(review_note) <= 1000",
    )
    op.create_index(
        "ix_workflows_in_review", "workflows", ["company_id", "submitted_for_review_at"],
        postgresql_where=sa.text("review_status = 'in_review' AND deleted_at IS NULL"),
    )

    op.create_table(
        "workflow_review_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("workflow_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("fingerprint", sa.Text(), nullable=True),
        sa.Column("simulation_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id", name="pk_workflow_review_events"),
        sa.ForeignKeyConstraint(
            ["workflow_id", "company_id"], ["workflows.id", "workflows.company_id"],
            name="fk_workflow_review_events_workflow", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"], ["users.id"],
            name="fk_workflow_review_events_actor", ondelete="SET NULL",
        ),
        sa.CheckConstraint(
            f"action IN {REVIEW_ACTIONS}", name="ck_workflow_review_events_action"
        ),
        sa.CheckConstraint(
            "note IS NULL OR char_length(note) <= 1000", name="ck_workflow_review_events_note"
        ),
    )
    op.create_index(
        "ix_workflow_review_events_workflow", "workflow_review_events",
        ["workflow_id", "created_at"],
    )
    op.execute(EVENTS_TRIGGER)
    op.execute(
        "CREATE TRIGGER workflow_review_events_append_only"
        " BEFORE UPDATE OR DELETE ON workflow_review_events"
        " FOR EACH ROW EXECUTE FUNCTION workflow_review_events_append_only()"
    )
    op.execute(WORKFLOW_TRIGGER)
    op.execute(CHILDREN_TRIGGER)
    # INSERT joins UPDATE and DELETE: a version must be born an unreviewed draft.
    op.execute("DROP TRIGGER IF EXISTS workflows_no_edit_when_published ON workflows")
    op.execute(
        "CREATE TRIGGER workflows_no_edit_when_published"
        " BEFORE INSERT OR UPDATE OR DELETE ON workflows"
        " FOR EACH ROW EXECUTE FUNCTION workflows_published_immutable()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS workflows_no_edit_when_published ON workflows")
    op.execute(
        "CREATE TRIGGER workflows_no_edit_when_published"
        " BEFORE UPDATE OR DELETE ON workflows"
        " FOR EACH ROW EXECUTE FUNCTION workflows_published_immutable()"
    )
    op.execute(OLD_CHILDREN_TRIGGER)
    op.execute(OLD_WORKFLOW_TRIGGER)
    op.execute(
        "DROP TRIGGER IF EXISTS workflow_review_events_append_only ON workflow_review_events"
    )
    op.execute("DROP FUNCTION IF EXISTS workflow_review_events_append_only()")
    op.drop_index("ix_workflow_review_events_workflow", table_name="workflow_review_events")
    op.drop_table("workflow_review_events")
    op.drop_index("ix_workflows_in_review", table_name="workflows")
    op.drop_constraint("ck_workflows_review_note_len", "workflows", type_="check")
    op.drop_constraint("ck_workflows_review_status", "workflows", type_="check")
    op.drop_constraint("fk_workflows_reviewed_by", "workflows", type_="foreignkey")
    op.drop_constraint("fk_workflows_submitted_by", "workflows", type_="foreignkey")
    for col in ("review_fingerprint", "review_note", "reviewed_by_user_id", "reviewed_at",
                "submitted_by_user_id", "submitted_for_review_at", "review_status"):
        op.drop_column("workflows", col)
