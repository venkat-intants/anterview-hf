"""stage_transitions: record round moves, and make the ledger append-only — B2.

Two gaps in the transition ledger.

**Round moves were invisible.** The ledger had from_status / to_status and
nothing else, and ``record_transition`` writes nothing when the status does not
change. Inside a workflow a candidate moves from round to round while their
status stays 'shortlisted', so every one of those moves — the stage changes a
hiring manager actually watches — left no trace. ``from_round_id`` and
``to_round_id`` record them.

They are plain references, not foreign keys. A ledger entry must outlive the
round it names: a workflow edited later, or a requisition removed, must not be
able to reach back and rewrite where a candidate was on a given day. (With a
foreign key, ON DELETE SET NULL would do exactly that — and the trigger below
would refuse it anyway.)

**Nothing stopped the history being edited.** "Append-only" was a comment. It is
now a trigger, in the manner of audit_log's, with two narrow exceptions that
exist so ordinary operations keep working:

* DELETE is allowed only when the enrolment it belongs to is already gone —
  i.e. the ON DELETE CASCADE from a deleted enrolment (or company). History can
  leave with its subject; it cannot be pruned while the subject remains.
* UPDATE is allowed only when it clears ``actor_user_id`` and changes nothing
  else — the ON DELETE SET NULL from a deleted user. The entry keeps saying
  that a person made the move, via ``automated = false``.

TRUNCATE is deliberately not blocked, unlike audit_log: it is a maintenance and
test-reset operation that empties the table, not an edit to anyone's history.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "c9e1a3b5d7f0"
down_revision: str | None = "b8d0f2a4c6e9"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("stage_transitions", sa.Column("from_round_id", sa.UUID(), nullable=True))
    op.add_column("stage_transitions", sa.Column("to_round_id", sa.UUID(), nullable=True))

    op.execute(
        """
        CREATE OR REPLACE FUNCTION stage_transitions_block_mutation() RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'DELETE'
               AND NOT EXISTS (SELECT 1 FROM enrolments WHERE id = OLD.enrolment_id) THEN
                -- The enrolment itself was deleted; its history goes with it.
                RETURN OLD;
            END IF;
            IF TG_OP = 'UPDATE'
               AND NEW.actor_user_id IS NULL AND OLD.actor_user_id IS NOT NULL
               AND ROW(NEW.id, NEW.company_id, NEW.enrolment_id, NEW.from_status,
                       NEW.to_status, NEW.automated, NEW.reason, NEW.occurred_at,
                       NEW.from_round_id, NEW.to_round_id)
                   IS NOT DISTINCT FROM
                   ROW(OLD.id, OLD.company_id, OLD.enrolment_id, OLD.from_status,
                       OLD.to_status, OLD.automated, OLD.reason, OLD.occurred_at,
                       OLD.from_round_id, OLD.to_round_id) THEN
                -- A deleted user's id being cleared (ON DELETE SET NULL).
                RETURN NEW;
            END IF;
            RAISE EXCEPTION
                'stage_transitions is append-only (B2 audit trail): % is not permitted', TG_OP;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER stage_transitions_no_mutation
        BEFORE UPDATE OR DELETE ON stage_transitions
        FOR EACH ROW EXECUTE FUNCTION stage_transitions_block_mutation();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS stage_transitions_no_mutation ON stage_transitions;")
    op.execute("DROP FUNCTION IF EXISTS stage_transitions_block_mutation();")
    op.drop_column("stage_transitions", "to_round_id")
    op.drop_column("stage_transitions", "from_round_id")
