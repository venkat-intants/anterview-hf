"""Task consent: the ledger and ``consented_at`` can no longer disagree — PH4-D4.

Revision ID: c8e0a2b4d6f8
Revises: a5d7f9b1c3e8
Create Date: 2026-09-22

WHY A NEW REVISION, NOT ANOTHER IN-PLACE EDIT
``a5d7f9b1c3e8`` was edited in place several times during review. That is
safe only for a database that never applied an earlier version of it, and
whether anyone ran the branch against the shared dev database is
unverified. So this revision RE-ASSERTS everything those edits changed, in
the shape they now have, whatever version of ``a5d7f9b1c3e8`` a database
applied:

- ``task_submissions_lifecycle()`` and ``task_responses_frozen()``,
  re-created from ``a5d7f9b1c3e8``'s own text (the lifecycle one with the
  addition below);
- ``ck_task_events_action``, with ``consent_withdrawn`` in its list;
- ``fk_task_submissions_accommodation`` and
  ``fk_task_submissions_superseded_by``, as ``ON DELETE RESTRICT``;
- the narrowed ``ix_dpdp_consent_active_unique`` and
  ``ix_dpdp_consent_task_submission_unique``.

On a database that applied the current ``a5d7f9b1c3e8``, every one of those
is a no-op in effect.

WHAT IS NEW: THE INVARIANT, BOTH WAYS
Every gate that decides whether a task's work may be read, downloaded,
auto-submitted or used to pass a round reads ``task_submissions.consented_at``.
The ledger (``dpdp_consent_ledger``) is the record of consent. Security
re-review found them disagreeing: an erasure REQUEST revokes every ledger
row for the user at once (admin_ops step 3b-iii), while ``consented_at``
stayed set until the executor ran 30 days later — the work kept being read
and could still pass a round (NEW-7). Both directions are now enforced here:

- ``consented_at`` may be SET only when this submission has an active
  ``assessment_submission`` ledger row (added to the lifecycle trigger's
  consent rule);
- REVOKING such a row, from anywhere, clears that submission's
  ``consented_at`` in the same transaction (``dpdp_task_consent_revoked``,
  an AFTER UPDATE trigger on the ledger).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

from alembic import op

revision: str = "c8e0a2b4d6f8"
down_revision: str | None = "a5d7f9b1c3e8"
branch_labels: str | None = None
depends_on: str | None = None


def _previous() -> ModuleType:
    """``a5d7f9b1c3e8``'s module, so its function bodies and constants are
    reused rather than copied — one source for what that revision means."""
    path = Path(__file__).with_name(
        "20260922_0002_a5d7f9b1c3e8_ph4_d4_job_simulations_portfolio.py"
    )
    spec = importlib.util.spec_from_file_location("_ph4_d4_a5d7f9b1c3e8", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_CONSENT_RULE = """    IF NEW.consented_at IS DISTINCT FROM OLD.consented_at AND NEW.consented_at IS NOT NULL THEN
        IF NOT (OLD.consented_at IS NULL AND OLD.status = 'assigned'
                AND NEW.status = 'in_progress') THEN
            RAISE EXCEPTION
                'task submission %: consent is given only at start, and afterwards only withdrawn',
                OLD.id;
        END IF;
    END IF;
"""

_CONSENT_RULE_WITH_LEDGER = """    IF NEW.consented_at IS DISTINCT FROM OLD.consented_at AND NEW.consented_at IS NOT NULL THEN
        IF NOT (OLD.consented_at IS NULL AND OLD.status = 'assigned'
                AND NEW.status = 'in_progress') THEN
            RAISE EXCEPTION
                'task submission %: consent is given only at start, and afterwards only withdrawn',
                OLD.id;
        END IF;
        -- ...and only with the ledger row that records it (c8e0a2b4d6f8).
        IF NOT EXISTS (
            SELECT 1 FROM dpdp_consent_ledger l
             WHERE l.consent_type = 'assessment_submission' AND l.granted
               AND l.revoked_at IS NULL AND l.evidence ->> 'submission_id' = OLD.id::text
        ) THEN
            RAISE EXCEPTION
                'task submission %: consent needs its dpdp_consent_ledger row first', OLD.id;
        END IF;
    END IF;
"""


def _lifecycle_with_ledger_check(previous: ModuleType) -> str:
    body: str = previous.TASK_SUBMISSIONS_LIFECYCLE
    if body.count(_CONSENT_RULE) != 1:
        raise RuntimeError(
            "a5d7f9b1c3e8's consent rule no longer matches the text this revision extends; "
            "update _CONSENT_RULE here to match it."
        )
    return body.replace(_CONSENT_RULE, _CONSENT_RULE_WITH_LEDGER)


LEDGER_REVOKE_FUNCTION = """
CREATE OR REPLACE FUNCTION dpdp_task_consent_revoked() RETURNS trigger AS $$
DECLARE
    sid text := NEW.evidence ->> 'submission_id';
BEGIN
    -- A task consent revoked ANYWHERE -- the task link, an erasure request,
    -- any path added later -- stops that submission being processed now:
    -- every gate reads consented_at. A redacted submission is left alone
    -- (the lifecycle trigger fixes it), and so is a malformed id, rather
    -- than failing the revocation itself.
    IF sid ~ '^[0-9a-fA-F-]{36}$' THEN
        UPDATE task_submissions SET consented_at = NULL, updated_at = now()
         WHERE id = sid::uuid AND consented_at IS NOT NULL AND redacted_at IS NULL;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""


def upgrade() -> None:
    previous = _previous()

    # Functions: re-created from a5d7f9b1c3e8's text (lifecycle: plus the
    # ledger check). CREATE OR REPLACE keeps the existing triggers bound.
    op.execute(_lifecycle_with_ledger_check(previous))
    op.execute(previous.TASK_RESPONSES_FROZEN)

    # ck_task_events_action, with every action a5d7f9b1c3e8 now lists.
    op.execute("ALTER TABLE task_events DROP CONSTRAINT IF EXISTS ck_task_events_action")
    op.execute(
        "ALTER TABLE task_events ADD CONSTRAINT ck_task_events_action"
        f" CHECK (action IN {previous.TASK_EVENT_ACTIONS})"
    )

    # The two composite FKs, RESTRICT (a composite SET NULL would null the
    # NOT NULL company_id).
    op.execute(
        "ALTER TABLE task_submissions DROP CONSTRAINT IF EXISTS fk_task_submissions_accommodation"
    )
    op.execute(
        "ALTER TABLE task_submissions ADD CONSTRAINT fk_task_submissions_accommodation"
        " FOREIGN KEY (accommodation_id, company_id)"
        " REFERENCES candidate_accommodations (id, company_id) ON DELETE RESTRICT"
    )
    op.execute(
        "ALTER TABLE task_submissions DROP CONSTRAINT IF EXISTS fk_task_submissions_superseded_by"
    )
    op.execute(
        "ALTER TABLE task_submissions ADD CONSTRAINT fk_task_submissions_superseded_by"
        " FOREIGN KEY (superseded_by_id, company_id)"
        " REFERENCES task_submissions (id, company_id) ON DELETE RESTRICT"
        " DEFERRABLE INITIALLY DEFERRED"
    )

    # The consent indexes, in their narrowed shape.
    op.execute("DROP INDEX IF EXISTS ix_dpdp_consent_active_unique")
    op.execute(previous._NEW_CONSENT_ACTIVE_UNIQUE_SQL)
    op.execute("DROP INDEX IF EXISTS ix_dpdp_consent_task_submission_unique")
    op.execute(previous._TASK_CONSENT_SUBMISSION_UNIQUE_SQL)

    # New: revoking a task consent clears consented_at.
    op.execute(LEDGER_REVOKE_FUNCTION)
    op.execute(
        "CREATE TRIGGER dpdp_task_consent_revoked"
        " AFTER UPDATE OF revoked_at ON dpdp_consent_ledger"
        " FOR EACH ROW WHEN (NEW.consent_type = 'assessment_submission'"
        " AND OLD.revoked_at IS NULL AND NEW.revoked_at IS NOT NULL)"
        " EXECUTE FUNCTION dpdp_task_consent_revoked()"
    )


def downgrade() -> None:
    previous = _previous()
    op.execute("DROP TRIGGER IF EXISTS dpdp_task_consent_revoked ON dpdp_consent_ledger")
    op.execute("DROP FUNCTION IF EXISTS dpdp_task_consent_revoked()")
    # Back to a5d7f9b1c3e8's own lifecycle function. The reconciled
    # constraints and indexes ARE a5d7f9b1c3e8's shape, so they stay.
    op.execute(previous.TASK_SUBMISSIONS_LIFECYCLE)
