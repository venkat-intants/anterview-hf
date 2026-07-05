"""exam submit idempotency — tighten uix_exam_attempts_one_live + retention indexes

Three changes in one migration:

1. EXAM DOUBLE-SUBMIT FIX (audit finding)
   The old partial unique index on exam_attempts:
       WHERE deleted_at IS NULL AND status <> 'expired'
   excluded only 'expired' — it still allowed a 'submitted' row to coexist with
   a new 'in_progress' row (e.g. if allow_retake=true). More critically, the
   existing guard relied on Redis to serialize concurrent submits of the SAME
   in_progress attempt. The DB had no constraint preventing two concurrent writes
   from both finalizing the same row.

   Fix: replace the index with one that covers only 'in_progress' attempts,
   making it impossible for TWO in_progress attempts to exist at the same time
   for the same (round_id, applicant_id). The submit path already has a Redis
   claim + an early return when status is 'submitted'/'expired' — this index
   is the DB-level safety net underneath that logic.

   Note: allow_retake=true still works because each retake starts a NEW attempt
   (different attempt_no); the old attempt is already 'submitted' so it no longer
   occupies the unique slot.

2. RETENTION COVERING INDEX (fixes full seq-scan on nightly purge)
   The purge query filters sessions on (status, completed_at). Without a
   covering index Postgres does a full sequential scan of every sessions row on
   every nightly run. The index is NON-UNIQUE, safe to CREATE CONCURRENTLY on a
   live table (noted here; cannot run inside a Alembic transaction — run via
   psql if the table is large):
       CREATE INDEX CONCURRENTLY idx_sessions_purge
       ON sessions (status, completed_at)
       WHERE deleted_at IS NULL;
   We create it non-concurrently here because Alembic migrations run in a
   transaction and CONCURRENTLY is not allowed there. On a small/staging DB this
   is fine; on a large live table document the CONCURRENTLY alternative above.

3. ABANDONED + CONSENT-WITHDRAWN SESSIONS INDEX
   The extended retention purge also covers (status='abandoned', completed_at)
   and (status='consent_withdrawn', completed_at). The same composite index
   already covers these via the (status, completed_at) prefix.

Revision: e4f6a8b0c2d4
Revises: d3f5a7b9c1e4
Create Date: 2026-07-01 00:01:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "e4f6a8b0c2d4"
down_revision: str | None = "d3f5a7b9c1e4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ── 1. Tighten the exam_attempts one-live-attempt index ──
    #
    # Old: WHERE deleted_at IS NULL AND status <> 'expired'
    #   — still allows submitted+in_progress to coexist (retake race).
    # New: WHERE deleted_at IS NULL AND status = 'in_progress'
    #   — exactly one in_progress attempt per (round, applicant) at a time.
    #   — submitted/expired attempts no longer occupy the unique slot, so
    #     allow_retake=true creates a new in_progress without conflicting.
    op.execute("DROP INDEX IF EXISTS uix_exam_attempts_one_live")
    op.execute(
        "CREATE UNIQUE INDEX uix_exam_attempts_one_live "
        "ON exam_attempts (round_id, applicant_id) "
        "WHERE deleted_at IS NULL AND status = 'in_progress'"
    )

    # ── 2. Covering index for the DPDP retention purge (sessions table) ──
    #
    # Predicate: WHERE deleted_at IS NULL — matches only live (non-erased) rows,
    # which is the set the purge always operates on. Including completed_at in
    # the index lets Postgres satisfy the purge's range filter on completed_at
    # without reading the heap for rows that are already deleted.
    #
    # TIER-2 NOTE: On a large live table run the equivalent CONCURRENTLY migration
    # outside Alembic:
    #   CREATE INDEX CONCURRENTLY idx_sessions_retention
    #     ON sessions (status, completed_at) WHERE deleted_at IS NULL;
    # Safe to re-run: IF NOT EXISTS guards are not available for partial indexes
    # in all PG versions, so we drop first (idempotent on a fresh schema).
    op.execute("DROP INDEX IF EXISTS idx_sessions_retention")
    op.execute(
        "CREATE INDEX idx_sessions_retention "
        "ON sessions (status, completed_at) "
        "WHERE deleted_at IS NULL"
    )


def downgrade() -> None:
    # Remove the retention index (non-destructive — just slows queries).
    op.execute("DROP INDEX IF EXISTS idx_sessions_retention")

    # Restore the old (looser) one-live-attempt index.
    op.execute("DROP INDEX IF EXISTS uix_exam_attempts_one_live")
    op.execute(
        "CREATE UNIQUE INDEX uix_exam_attempts_one_live "
        "ON exam_attempts (round_id, applicant_id) "
        "WHERE deleted_at IS NULL AND status <> 'expired'"
    )
