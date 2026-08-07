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


def test_the_index_no_longer_carries_a_predicate_the_query_lacks() -> None:
    source = _MIGRATION.read_text(encoding="utf-8")
    upgrade = source.split("def upgrade()", 1)[1].split("def downgrade()", 1)[0]
    assert "CREATE INDEX idx_sessions_retention ON sessions (status, completed_at)" in upgrade
    assert "WHERE deleted_at IS NULL" not in upgrade


def test_the_updated_at_branch_gets_an_index_of_its_own() -> None:
    """``status IN ('abandoned','failed') AND updated_at < cutoff`` had no usable
    index at all — ``updated_at`` was not in the original index's column list."""
    source = _MIGRATION.read_text(encoding="utf-8")
    upgrade = source.split("def upgrade()", 1)[1].split("def downgrade()", 1)[0]
    assert "idx_sessions_retention_updated ON sessions (status, updated_at)" in upgrade


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
