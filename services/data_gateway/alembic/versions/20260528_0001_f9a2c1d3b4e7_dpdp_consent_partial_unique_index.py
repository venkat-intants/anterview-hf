"""dpdp_consent_partial_unique_index

Sprint 4 — S4-009: Add partial unique index on dpdp_consent_ledger to make the
DPDP consent POST endpoint race-proof at the database layer.

Problem (security-auditor HIGH-1, S3-011):
    The idempotency check in data_gateway/app/routers/consent.py is TOCTOU-vulnerable.
    Two concurrent POSTs from the same user can both pass the "no existing active row"
    SELECT before either INSERT commits. The result is two active consent rows for the
    same user — duplicating the ledger entry and potentially producing inconsistent
    revocation behaviour later.

Solution:
    Add a partial unique index on (user_id, consent_type, purpose) filtered to
    rows where granted = TRUE AND revoked_at IS NULL. This is the database-level
    guard that catches the race condition: whichever concurrent INSERT commits
    second will raise IntegrityError (unique_violation, SQLSTATE 23505). The
    application layer handles that in a try/except and re-queries to return the
    winning row (idempotent 200 path).

Index name: ix_dpdp_consent_active_unique

CONCURRENTLY note:
    PostgreSQL requires CREATE INDEX CONCURRENTLY to run outside an explicit
    transaction. The alembic env.py uses context.begin_transaction() in
    do_run_migrations(), which wraps the entire migration in a transaction.
    CONCURRENTLY inside a transaction raises:
      ERROR: CREATE INDEX CONCURRENTLY cannot run inside a transaction block.

    On the demo tier (Neon managed Postgres, small table) we therefore use a
    plain CREATE UNIQUE INDEX (no CONCURRENTLY). This takes an
    AccessShareLock on the table for the duration of index creation, which is
    acceptable: the table is empty or very small at this stage and the lock
    duration is well under 1 second. The downgrade mirror also uses plain DROP.

    If this migration is run on a high-traffic production table (Sprint 6+),
    replace with a CONCURRENTLY build by extracting the op.execute() calls
    into a separate migration and setting ``transactional_ddl = False`` via
    a custom env.py path (see Alembic docs §"Working with Transactional DDL").

Revision ID: f9a2c1d3b4e7
Revises:     eda3829ec95a
Create Date: 2026-05-28 00:01:00.000000
"""

from collections.abc import Sequence

from alembic import op

# Alembic revision identifiers.
revision: str = "f9a2c1d3b4e7"
down_revision: str | None = "eda3829ec95a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add partial unique index to enforce one active consent row per user+type+purpose.

    The WHERE clause mirrors the "active consent" predicate used by both
    data_gateway/app/routers/consent.py (_find_active_consent) and
    interview_core/app/consent_guard.py (has_active_consent):
        granted = TRUE AND revoked_at IS NULL

    Only active (non-revoked) rows are covered, so:
      - A user can revoke consent and then re-grant it (new row, no conflict).
      - Historical (revoked) rows are preserved for the DPDP audit trail.
      - The unique guard only prevents concurrent double-insert of active rows.
    """
    op.execute(
        "CREATE UNIQUE INDEX ix_dpdp_consent_active_unique "
        "ON dpdp_consent_ledger (user_id, consent_type, purpose) "
        "WHERE granted = TRUE AND revoked_at IS NULL"
    )


def downgrade() -> None:
    """Drop the partial unique index."""
    op.execute("DROP INDEX IF EXISTS ix_dpdp_consent_active_unique")
