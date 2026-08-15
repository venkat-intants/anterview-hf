"""DPDP-9 — the purge predicate and the index it was built for must agree.

``idx_sessions_retention`` carried ``WHERE deleted_at IS NULL`` while
``_purge_predicate`` emitted no such clause. A partial index is only usable when
the planner can prove the query's WHERE implies the index predicate, so the index
could never serve the query it was created for: the nightly purge
sequential-scanned a table sized for 20 lakh users, every night, while looking
indexed.

The alignment was made by dropping the partial predicate from the index
(migration 20260807_0001), NOT by adding the clause here — a soft-deleted session
is one pending DPDP erasure, the last row a deletion job should skip. These tests
pin that direction, so a future "tidy-up" that adds ``deleted_at IS NULL`` to the
predicate fails loudly instead of silently retaining erasure-pending rows
forever.
"""

from __future__ import annotations

import ast
from datetime import UTC, datetime
from pathlib import Path

from app import retention

_MIGRATION = (
    Path(retention.__file__).parents[1]
    / "alembic"
    / "versions"
    / "20260807_0001_b4d6f8a0c2e5_retention_index_alignment.py"
)


def _sql() -> str:
    predicate = retention._purge_predicate(datetime(2026, 5, 1, tzinfo=UTC))
    return str(predicate.compile(compile_kwargs={"literal_binds": True}))


def test_the_predicate_does_not_filter_on_deleted_at() -> None:
    """Adding the clause would exclude erasure-pending rows from the one job that
    would otherwise remove them."""
    assert "deleted_at" not in _sql()


def _upgrade_statements() -> list[str]:
    """The SQL ``upgrade()`` executes, one normalised statement per entry.

    Parsed with ``ast`` rather than sliced out of the file as text, which is what
    these tests used to do. Two reasons, both of which have bitten:

    * **Comments are not SQL.** The migration's own prose names the indexes it
      manages, so a substring search over the raw source can be satisfied by a
      comment ABOUT a statement that is no longer there.
    * **Formatting is not meaning.** Python concatenates adjacent string
      literals, so wrapping one ``op.execute`` across two lines changed nothing
      about the emitted SQL and still broke the assertion. ``ast`` folds them
      back into one constant, so these tests fail only when the SQL changes.
    """
    tree = ast.parse(_MIGRATION.read_text(encoding="utf-8"))
    upgrade = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "upgrade"
    )
    return [
        " ".join(node.args[0].value.split())
        for node in ast.walk(upgrade)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "execute"
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, str)
    ]


def test_the_index_no_longer_carries_a_predicate_the_query_lacks() -> None:
    statements = _upgrade_statements()
    assert (
        "CREATE INDEX idx_sessions_retention ON sessions (status, completed_at)" in statements
    )
    # Only the upgrade. downgrade() restores the partial index on purpose.
    assert not any("WHERE deleted_at IS NULL" in s for s in statements)


def test_the_updated_at_branch_gets_an_index_of_its_own() -> None:
    """``status IN ('abandoned','failed') AND updated_at < cutoff`` had no usable
    index at all — ``updated_at`` was not in the original index's column list."""
    assert any(
        "idx_sessions_retention_updated ON sessions (status, updated_at)" in s
        for s in _upgrade_statements()
    )


def test_the_updated_index_is_created_idempotently_and_never_dropped() -> None:
    """It is NEW in this migration, so no wrong prior definition exists to
    replace — unlike ``idx_sessions_retention``, which must be dropped because
    the name already carries the unusable partial index.

    That asymmetry is load-bearing on a large live table. The module docstring
    tells an operator to pre-create both CONCURRENTLY and let the migration find
    them present; a DROP here would discard that work and rebuild the index
    inside Alembic's transaction, taking exactly the ACCESS EXCLUSIVE lock the
    CONCURRENTLY recipe exists to avoid.
    """
    statements = _upgrade_statements()

    creates = [s for s in statements if "CREATE" in s and "idx_sessions_retention_updated" in s]
    assert creates == [
        "CREATE INDEX IF NOT EXISTS idx_sessions_retention_updated "
        "ON sessions (status, updated_at)"
    ]
    assert not any(
        "DROP" in s and "idx_sessions_retention_updated" in s for s in statements
    )

    # The other index keeps drop-then-create, and without IF NOT EXISTS: a
    # silent no-op there would leave the unusable partial index in place, which
    # is the whole point of the migration.
    assert "DROP INDEX IF EXISTS idx_sessions_retention" in statements


def test_every_predicate_branch_is_covered_by_one_of_the_two_indexes() -> None:
    """The purge's three OR branches filter on (status, completed_at),
    (status, updated_at) and status alone. Anything else would need a third
    index, so pin the column set the predicate is allowed to touch."""
    sql = _sql()
    for column in ("status", "completed_at", "updated_at"):
        assert column in sql
    assert "presenter_id" not in sql
    assert "duration_seconds" not in sql


# ---------------------------------------------------------------------------
# DPDP-6 — the retention rule the status table has always claimed
# ---------------------------------------------------------------------------
def test_consent_withdrawn_is_purged_without_waiting_out_the_window() -> None:
    """DPDP §6(4): storing a recording IS processing, so holding a withdrawn
    candidate's session for the remainder of a 90-day window is the one status
    where waiting is the non-compliant choice. The module docstring has claimed
    this rule since the status was introduced; the predicate did not implement
    it and applied the ordinary N-day window instead."""
    sql = _sql()
    # A bare equality standing as its own OR branch — no timestamp comparison
    # anywhere after it, which is what "regardless of age" means in SQL.
    branch = sql.rsplit(" OR ", 1)[1]
    assert branch == "sessions.status = 'consent_withdrawn'"
    assert "consent_withdrawn" not in sql.rsplit(" OR ", 1)[0]


def test_the_other_terminal_statuses_still_wait_out_the_window() -> None:
    """The relaxation is scoped to withdrawal. An abandoned session must not
    start being deleted the night after it is abandoned."""
    sql = _sql()
    assert "sessions.updated_at < '2026-05-01" in sql
    assert "sessions.completed_at < '2026-05-01" in sql
    assert retention._STALENESS_STATUSES == ("abandoned", "failed")
