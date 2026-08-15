"""retention indexes actually match the purge predicate (DPDP-9)

Migration 20260701_0001 added::

    CREATE INDEX idx_sessions_retention
      ON sessions (status, completed_at)
      WHERE deleted_at IS NULL;

``app/retention.py::_purge_predicate`` has never emitted a ``deleted_at IS NULL``
clause. A partial index can only be used when the planner can prove the query's
WHERE implies the index predicate, so this index was unusable by the one query it
was created for: the nightly purge sequential-scanned a table sized for 20 lakh
users, every night, and looked indexed while doing it.

Two ways to align. Adding ``deleted_at IS NULL`` to the predicate would EXCLUDE
soft-deleted sessions from the purge — rows that are pending DPDP erasure, i.e.
the last rows a deletion job should skip, and rows that would then be retained
forever if the erasure executor never ran. So the alignment is made from the
other side: drop the partial predicate.

Second gap, not in the original finding but visible once the predicate and the
index are read side by side: the purge's other branch filters
``status IN ('abandoned','failed') AND updated_at < cutoff``. ``updated_at`` is
not in ``idx_sessions_retention`` at all, so that branch had no usable index
either. ``idx_sessions_retention_updated`` covers it.

Both are plain (non-unique, non-partial) B-trees on (status, <timestamp>), which
is the shape the two OR branches want. The third branch —
``status = 'consent_withdrawn'`` with no time condition — is served by the
``status`` prefix of either.

CONCURRENTLY note: both statements below run inside Alembic's transaction, which
forbids CREATE INDEX CONCURRENTLY. That is fine on a fresh or small database. On
a large live table, run the equivalents outside Alembic first and let this
migration find them already present::

    DROP INDEX CONCURRENTLY IF EXISTS idx_sessions_retention;
    CREATE INDEX CONCURRENTLY idx_sessions_retention
      ON sessions (status, completed_at);
    CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_sessions_retention_updated
      ON sessions (status, updated_at);

Revision: b4d6f8a0c2e5
Revises: a1b2c3d4e5f7
Create Date: 2026-08-07 00:01:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "b4d6f8a0c2e5"
down_revision: str | None = "a1b2c3d4e5f7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Drop-then-create rather than IF NOT EXISTS: the index NAME already exists
    # with the wrong (partial) definition, so a CREATE ... IF NOT EXISTS would
    # succeed silently and leave the unusable index in place — the failure mode
    # this migration exists to remove.
    op.execute("DROP INDEX IF EXISTS idx_sessions_retention")
    op.execute(
        "CREATE INDEX idx_sessions_retention ON sessions (status, completed_at)"
    )

    # IF NOT EXISTS, and NO drop first — the opposite of the pair above, because
    # this index has no wrong prior definition to remove. It is new in this
    # migration, so anything already carrying the name was created by the
    # CONCURRENTLY recipe in the module docstring: dropping and rebuilding it
    # would take the ACCESS EXCLUSIVE lock that recipe exists to avoid, inside
    # Alembic's transaction, on the large live table it was run for. Finding it
    # already present is the intended outcome, not a collision.
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_sessions_retention_updated "
        "ON sessions (status, updated_at)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_sessions_retention_updated")

    # Restore the partial index exactly as 20260701_0001 created it, so a
    # downgrade lands on the schema that migration describes — including its
    # defect. A downgrade that "fixes" something is a downgrade that cannot be
    # reasoned about.
    op.execute("DROP INDEX IF EXISTS idx_sessions_retention")
    op.execute(
        "CREATE INDEX idx_sessions_retention "
        "ON sessions (status, completed_at) "
        "WHERE deleted_at IS NULL"
    )
