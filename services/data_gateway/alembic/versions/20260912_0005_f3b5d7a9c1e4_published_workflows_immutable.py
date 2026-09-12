"""A published workflow cannot change, and only one is live per opening (C1).

Revision ID: f3b5d7a9c1e4
Revises: e2a4c6b8d0f1
Create Date: 2026-09-12

C1's promise is that a candidate finishes on the version they started: editing
a published workflow creates version n+1, and the enrolment pins the version.
``app/workflows.py`` enforces that on every structural writer (``_assert_draft``)
— but only there. A stray UPDATE, a future importer, a fix applied by hand in
psql at 2am, all of them could still change a threshold under people who had
already sat the round, and nothing would say so afterwards.

The audit log and the stage ledger already have this guard at the database. A
promise this size belongs in the same place, for the same reason: the
application is where the rule is explained, the database is where it holds.

WHAT IS STILL ALLOWED on a published workflow, because publishing the next
version needs it: the status moving to 'archived', ``published_at``,
``updated_at`` and a soft delete. Everything else — thresholds, settings,
rounds, criteria — is refused while the row says 'published'. Going back to
'draft' is refused too: that would be editing by another name.

A DELETE is allowed only when the parent requisition is already gone, which is
the cascade. Same test the stage-transition trigger uses.

ALSO: a partial unique index, so one opening cannot have two live workflows.
``publish()`` archives the previous one in the same transaction, so this holds
today by construction; the index is what keeps it true when two people publish
at the same moment. Rows that already violate it are repaired first — the
newest published version wins, which is exactly what ``publish()`` would have
done.
"""

from __future__ import annotations

from alembic import op

revision: str = "f3b5d7a9c1e4"
down_revision: str | None = "e2a4c6b8d0f1"
branch_labels = None
depends_on = None


WORKFLOW_TRIGGER = """
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

WORKFLOW_TRIGGER_HOOK = """
CREATE TRIGGER workflows_no_edit_when_published
    BEFORE UPDATE OR DELETE ON workflows
    FOR EACH ROW EXECUTE FUNCTION workflows_published_immutable()
"""

# Rounds and criteria belong to their workflow: "published is immutable" has to
# cover them, or the guard above protects the settings and leaves the rounds —
# which is where the thresholds actually live.
CHILDREN_TRIGGER = """
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
    -- No workflow (or no round): the parent is already gone, so this is the
    -- cascade cleaning up after it.
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

ROUNDS_TRIGGER_HOOK = """
CREATE TRIGGER workflow_rounds_no_edit_when_published
    BEFORE INSERT OR UPDATE OR DELETE ON workflow_rounds
    FOR EACH ROW EXECUTE FUNCTION workflow_children_immutable()
"""

CRITERIA_TRIGGER_HOOK = """
CREATE TRIGGER round_criteria_no_edit_when_published
    BEFORE INSERT OR UPDATE OR DELETE ON round_criteria
    FOR EACH ROW EXECUTE FUNCTION workflow_children_immutable()
"""

ONE_LIVE_VERSION = """
DO $$
DECLARE
    fixed int;
BEGIN
    WITH ranked AS (
        SELECT id, row_number() OVER (
                   PARTITION BY requisition_id
                   ORDER BY version DESC, published_at DESC NULLS LAST
               ) AS rn
          FROM workflows
         WHERE status = 'published' AND deleted_at IS NULL
    )
    UPDATE workflows w SET status = 'archived', updated_at = now()
      FROM ranked WHERE w.id = ranked.id AND ranked.rn > 1;
    GET DIAGNOSTICS fixed = ROW_COUNT;
    IF fixed > 0 THEN
        RAISE NOTICE 'archived % superseded published workflow(s)', fixed;
    END IF;
END
$$
"""

ONE_LIVE_INDEX = """
CREATE UNIQUE INDEX IF NOT EXISTS uq_workflows_one_published
    ON workflows (requisition_id)
 WHERE status = 'published' AND deleted_at IS NULL
"""


def upgrade() -> None:
    # The repair runs before the triggers exist, so it is not blocked by them.
    # One statement per execute: asyncpg prepares each and refuses a batch.
    # Every step is idempotent, so a half-applied attempt can simply be re-run.
    op.execute("DROP TRIGGER IF EXISTS workflows_no_edit_when_published ON workflows")
    op.execute(
        "DROP TRIGGER IF EXISTS workflow_rounds_no_edit_when_published ON workflow_rounds"
    )
    op.execute("DROP TRIGGER IF EXISTS round_criteria_no_edit_when_published ON round_criteria")
    op.execute(ONE_LIVE_VERSION)
    op.execute(ONE_LIVE_INDEX)
    op.execute(WORKFLOW_TRIGGER)
    op.execute(WORKFLOW_TRIGGER_HOOK)
    op.execute(CHILDREN_TRIGGER)
    op.execute(ROUNDS_TRIGGER_HOOK)
    op.execute(CRITERIA_TRIGGER_HOOK)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS round_criteria_no_edit_when_published ON round_criteria")
    op.execute("DROP TRIGGER IF EXISTS workflow_rounds_no_edit_when_published ON workflow_rounds")
    op.execute("DROP FUNCTION IF EXISTS workflow_children_immutable()")
    op.execute("DROP TRIGGER IF EXISTS workflows_no_edit_when_published ON workflows")
    op.execute("DROP FUNCTION IF EXISTS workflows_published_immutable()")
    op.execute("DROP INDEX IF EXISTS uq_workflows_one_published")
