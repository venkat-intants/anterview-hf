"""AR-5: let erasure redact a decision's free-text rationale, in place.

Revision ID: f2a4c6e8b0d3
Revises: e4a6c8b0d2f7
Create Date: 2026-09-26

THE GAP THIS CLOSES
``docs/ACCEPTED-RISKS.md`` AR-5 recorded that the free text a person types when
they hire or reject a candidate — ``stage_transitions.reason`` and
``audit_log.details.{reason,rationale}`` on the two decision actions — survives
DPDP erasure, because both tables are append-only and neither trigger had a
redaction exception. ``audit_log_block_mutation()`` refused every UPDATE
outright; ``stage_transitions_block_mutation()`` permitted exactly one shape
(a deleted actor's id being cleared). Redacting either column needed a new,
narrow, structurally-enforced exception — this migration adds it, on the
``interviewer_scorecards_protect()`` pattern (migration ``d2f4a6c8e0b1``):
redaction is a one-way transition, marked by a timestamp, and it is the ONLY
thing that transition may change.

WHAT CHANGES
1. ``stage_transitions`` gets a ``redacted_at`` column. The trigger gains a
   third permitted shape: ``redacted_at`` NULL -> now(), ``reason`` moving to
   the fixed marker ``'[redacted]'`` (or staying NULL), and NOTHING else on the
   row changing — not even ``actor_user_id``, which the existing "cleared by a
   deleted user" branch already owns. A CHECK constraint holds the same
   invariant independently of the trigger: a redacted row's reason is always
   NULL or the marker, never live prose.
2. ``audit_log`` gets a ``redacted_at`` column. The trigger gains one permitted
   shape: ``redacted_at`` NULL -> now(), and the ONLY thing that may differ in
   ``details`` is the ``reason`` and/or ``rationale`` key, and only by becoming
   the fixed marker. Every other column and every other key of ``details`` —
   who decided, what, when, the reason CODE and CATEGORY, whether it was a
   reversal — is frozen, exactly as the append-only trigger already required.
   A matching CHECK constraint holds the same invariant independently of the
   trigger.

Both changes are deliberately SHAPE-based, not keyed to today's three action
names (``enrolment.decision.*``, ``applicant.decision.*``,
``enrolment.reapply_override``). A future action that reuses the ``reason`` /
``rationale`` key on a decision-like row is covered without a second
migration; anything that tries to rewrite those keys to something other than
the marker, or touches any other column, is still refused.

WHY NOT JUST STOP WRITING THE PROSE
AR-5's path to closure named two options: stop copying the rationale into
``audit_log.details`` (keep has-reason and length, as the PH4-A1 scorecards
already do), or add a redaction path the append-only trigger permits. The first
would still leave the ALREADY-WRITTEN rows of the previous years unredactable
forever. This migration takes the second path, which reaches every row already
on file as well as every future one, and stops writing new unredactable prose
nowhere — the columns are unchanged; only reprocessing an ERASED candidate's
own rows is now possible.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "f2a4c6e8b0d3"
down_revision: str | None = "e4a6c8b0d2f7"
branch_labels: str | None = None
depends_on: str | None = None


STAGE_TRANSITIONS_TRIGGER = """
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
    IF TG_OP = 'UPDATE'
       AND OLD.redacted_at IS NULL AND NEW.redacted_at IS NOT NULL
       AND ROW(NEW.id, NEW.company_id, NEW.enrolment_id, NEW.from_status,
               NEW.to_status, NEW.actor_user_id, NEW.automated, NEW.occurred_at,
               NEW.from_round_id, NEW.to_round_id)
           IS NOT DISTINCT FROM
           ROW(OLD.id, OLD.company_id, OLD.enrolment_id, OLD.from_status,
               OLD.to_status, OLD.actor_user_id, OLD.automated, OLD.occurred_at,
               OLD.from_round_id, OLD.to_round_id)
       AND (NEW.reason IS NOT DISTINCT FROM OLD.reason OR NEW.reason = '[redacted]') THEN
        -- DPDP erasure (AR-5, closed): the reason a person wrote is redacted,
        -- once, together with a stamp that says so. Everything else about the
        -- move (who, when, from what, to what) is untouched -- the ledger
        -- still proves a person decided, only not what they wrote.
        RETURN NEW;
    END IF;
    RAISE EXCEPTION
        'stage_transitions is append-only (B2 audit trail): % is not permitted', TG_OP;
END;
$$ LANGUAGE plpgsql;
"""

STAGE_TRANSITIONS_TRIGGER_DOWN = """
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

AUDIT_LOG_TRIGGER = """
CREATE OR REPLACE FUNCTION audit_log_block_mutation() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'UPDATE'
       AND OLD.redacted_at IS NULL AND NEW.redacted_at IS NOT NULL
       AND ROW(NEW.event_id, NEW.actor_id, NEW.actor_type, NEW.action, NEW.resource_type,
               NEW.resource_id, NEW.ip_address, NEW.user_agent, NEW.event_ts)
           IS NOT DISTINCT FROM
           ROW(OLD.event_id, OLD.actor_id, OLD.actor_type, OLD.action, OLD.resource_type,
               OLD.resource_id, OLD.ip_address, OLD.user_agent, OLD.event_ts)
       AND (NEW.details - 'reason' - 'rationale')
           IS NOT DISTINCT FROM (OLD.details - 'reason' - 'rationale')
       AND (NEW.details ->> 'reason' IS NOT DISTINCT FROM OLD.details ->> 'reason'
            OR NEW.details ->> 'reason' = '[redacted]')
       AND (NEW.details ->> 'rationale' IS NOT DISTINCT FROM OLD.details ->> 'rationale'
            OR NEW.details ->> 'rationale' = '[redacted]') THEN
        -- DPDP erasure (AR-5, closed): a decision rationale that named or
        -- described the erased applicant is replaced with a fixed marker.
        -- Every other key of `details` (reason_code, reason_label, reversal,
        -- ids, counts) and every other column stay exactly as recorded -- the
        -- trail still proves a person decided and why-in-category, only not
        -- the prose.
        RETURN NEW;
    END IF;
    RAISE EXCEPTION
        'audit_log is append-only (DPDP audit integrity): % is not permitted', TG_OP;
END;
$$ LANGUAGE plpgsql;
"""

AUDIT_LOG_TRIGGER_DOWN = """
CREATE OR REPLACE FUNCTION audit_log_block_mutation() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION
        'audit_log is append-only (DPDP audit integrity): % is not permitted', TG_OP;
END;
$$ LANGUAGE plpgsql;
"""


def upgrade() -> None:
    op.add_column(
        "stage_transitions",
        sa.Column("redacted_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.execute(
        "ALTER TABLE stage_transitions ADD CONSTRAINT ck_stage_transitions_redacted "
        "CHECK (redacted_at IS NULL OR reason IS NULL OR reason = '[redacted]')"
    )
    op.execute(STAGE_TRANSITIONS_TRIGGER)

    op.add_column(
        "audit_log",
        sa.Column("redacted_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.execute(
        "ALTER TABLE audit_log ADD CONSTRAINT ck_audit_log_redacted "
        "CHECK (redacted_at IS NULL OR ("
        "  (NOT (details ? 'reason') OR details ->> 'reason' = '[redacted]')"
        "  AND (NOT (details ? 'rationale') OR details ->> 'rationale' = '[redacted]')"
        "))"
    )
    op.execute(AUDIT_LOG_TRIGGER)


def downgrade() -> None:
    op.execute(AUDIT_LOG_TRIGGER_DOWN)
    op.execute("ALTER TABLE audit_log DROP CONSTRAINT IF EXISTS ck_audit_log_redacted")
    op.drop_column("audit_log", "redacted_at")

    op.execute(STAGE_TRANSITIONS_TRIGGER_DOWN)
    op.execute(
        "ALTER TABLE stage_transitions DROP CONSTRAINT IF EXISTS ck_stage_transitions_redacted"
    )
    op.drop_column("stage_transitions", "redacted_at")
