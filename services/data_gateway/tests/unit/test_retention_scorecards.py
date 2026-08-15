"""DPDP-4 — the retention purge must take the scorecard and its objects with it.

The purge used to issue exactly one statement, ``DELETE FROM sessions``, and rely
on FK cascade for the rest. ``scorecards.session_id`` is deliberately NOT a
foreign key (``models.py``: the table is written by feedback_billing while
sessions live here), so nothing cascaded: after the 90-day purge the scorecard
row survived indefinitely holding ``summary`` — a verdict on the candidate —
plus ``strengths``, ``improvements``, ``scores`` and ``composite_score``, and
live R2 keys for a PDF and a transcript that were never deleted. Worse, it was
then an ORPHAN: no session to join back to, so invisible to every session-scoped
erasure or audit query. The job reported success (DPDP §8(7), CWE-459).

These run without a database. The integration suite in
``tests/integration/test_retention.py`` covers the same job against real
Postgres, and is deselected in CI for want of one (DPDP-2) — which is exactly why
the ordering guarantees below are pinned here, where they actually run.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import MagicMock

import pytest
from sqlalchemy import Delete, Select
from sqlalchemy.sql.functions import Function

from app import retention
from app.config import settings as _app_settings
from app.models import Scorecard
from app.models import Session as InterviewSession
from app.s3_upload import StorageNotConfiguredError

_SESSION_IDS = [uuid.uuid4(), uuid.uuid4()]


def _settings(**overrides: Any) -> Any:
    base = {
        "retention_dry_run": False,
        "retention_days": 90,
        "s3_endpoint": "https://example.r2.cloudflarestorage.com",
        "s3_access_key_id": "key",
        "s3_secret_access_key": "secret",
        "s3_scorecard_bucket": "scorecards-bucket",
    }
    base.update(overrides)
    return _app_settings.model_copy(update=base)


def _target_table(stmt: Any) -> str:
    """Name the table a statement addresses, so the fake DB can answer in kind."""
    if isinstance(stmt, Delete):
        return str(stmt.table.name)
    if isinstance(stmt, Select):
        first = stmt.column_descriptions[0]
        # The count check comes first: ``func.count(Session.id)`` still reports
        # ``entity=Session``, so entity alone cannot tell the dry-run count from
        # the live id fetch.
        if isinstance(first.get("expr"), Function):
            return "count"
        entity = first.get("entity")
        if entity is InterviewSession:
            return "sessions"
        if entity is Scorecard:
            return "scorecards"
    raise AssertionError(f"unexpected statement: {stmt!r}")


class _FakeDb:
    """Minimal AsyncSession stand-in that records the ORDER of what it was asked.

    Order is the whole point of this fix, so the assertion surface is the
    recorded sequence rather than a set of call counts.
    """

    def __init__(
        self,
        *,
        session_ids: list[uuid.UUID],
        scorecard_rows: list[tuple[str | None, str | None]],
    ) -> None:
        self.session_ids = session_ids
        self.scorecard_rows = scorecard_rows
        self.calls: list[str] = []
        self.committed = False

    async def execute(self, stmt: Any) -> Any:
        table = _target_table(stmt)
        verb = "delete" if isinstance(stmt, Delete) else "select"
        self.calls.append(f"{verb}:{table}")

        result = MagicMock()
        if verb == "delete":
            result.rowcount = (
                len(self.session_ids) if table == "sessions" else len(self.scorecard_rows)
            )
            return result
        if table == "sessions":
            result.scalars.return_value.all.return_value = list(self.session_ids)
        elif table == "scorecards":
            result.all.return_value = list(self.scorecard_rows)
        else:
            result.scalar_one.return_value = len(self.session_ids)
        return result

    async def commit(self) -> None:
        self.committed = True
        self.calls.append("commit")


@pytest.fixture
def deleted_buckets(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[str]]:
    """Capture what the purge asked object storage to delete."""
    captured: dict[str, list[str]] = {}

    async def _fake_delete(keys_by_bucket: dict[str, list[str]], *, settings: Any) -> int:
        captured.update(keys_by_bucket)
        return sum(len(v) for v in keys_by_bucket.values())

    monkeypatch.setattr("app.s3_upload.delete_objects", _fake_delete)
    return captured


# ---------------------------------------------------------------------------
# The fix
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_purge_deletes_the_scorecard_rows_and_their_objects(
    deleted_buckets: dict[str, list[str]],
) -> None:
    db = _FakeDb(
        session_ids=_SESSION_IDS,
        scorecard_rows=[
            ("scorecards/a.pdf", "transcripts/a.json"),
            ("scorecards/b.pdf", None),
        ],
    )

    deleted = await retention.purge_expired_sessions(db=db, settings=_settings())  # type: ignore[arg-type]

    assert deleted == len(_SESSION_IDS)
    assert "delete:scorecards" in db.calls
    assert deleted_buckets == {
        "scorecards-bucket": [
            "scorecards/a.pdf",
            "transcripts/a.json",
            "scorecards/b.pdf",
        ]
    }


@pytest.mark.asyncio
async def test_keys_are_collected_before_anything_is_deleted(
    deleted_buckets: dict[str, list[str]],
) -> None:
    """Collect-before-delete, the discipline ``erasure_executor.py`` establishes.
    Reading the keys after the DELETE would find nothing and orphan every object
    — the exact shape of the M-1a bug in the erasure path."""
    db = _FakeDb(session_ids=_SESSION_IDS, scorecard_rows=[("scorecards/a.pdf", None)])

    await retention.purge_expired_sessions(db=db, settings=_settings())  # type: ignore[arg-type]

    assert db.calls == [
        "select:sessions",
        "select:scorecards",
        "delete:scorecards",
        "delete:sessions",
        "commit",
    ]


@pytest.mark.asyncio
async def test_sessions_are_deleted_after_the_scorecards_that_reference_them(
    deleted_buckets: dict[str, list[str]],
) -> None:
    db = _FakeDb(session_ids=_SESSION_IDS, scorecard_rows=[("scorecards/a.pdf", None)])

    await retention.purge_expired_sessions(db=db, settings=_settings())  # type: ignore[arg-type]

    assert db.calls.index("delete:scorecards") < db.calls.index("delete:sessions")


@pytest.mark.asyncio
async def test_null_object_keys_are_skipped_not_sent_as_none(
    deleted_buckets: dict[str, list[str]],
) -> None:
    """report_pdf_key / transcript_key are NULL until generated, which is the
    common case; a None reaching the S3 client would fail the whole purge."""
    db = _FakeDb(session_ids=_SESSION_IDS, scorecard_rows=[(None, None)])

    await retention.purge_expired_sessions(db=db, settings=_settings())  # type: ignore[arg-type]

    assert deleted_buckets == {"scorecards-bucket": []}
    assert "delete:scorecards" in db.calls


@pytest.mark.asyncio
async def test_duplicate_keys_are_deduplicated(
    deleted_buckets: dict[str, list[str]],
) -> None:
    """objects_deleted is the number an auditor reads; deleting one key twice is
    harmless but inflates it."""
    db = _FakeDb(
        session_ids=_SESSION_IDS,
        scorecard_rows=[("scorecards/a.pdf", None), ("scorecards/a.pdf", None)],
    )

    await retention.purge_expired_sessions(db=db, settings=_settings())  # type: ignore[arg-type]

    assert deleted_buckets == {"scorecards-bucket": ["scorecards/a.pdf"]}


# ---------------------------------------------------------------------------
# Failure must abort BEFORE any row is deleted
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_a_storage_failure_aborts_before_the_row_delete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Leaving the rows for the next run is recoverable. Deleting them anyway
    leaves objects in the bucket that nothing points at, which is not."""

    async def _boom(keys_by_bucket: dict[str, list[str]], *, settings: Any) -> int:
        raise StorageNotConfiguredError("no endpoint")

    monkeypatch.setattr("app.s3_upload.delete_objects", _boom)
    db = _FakeDb(session_ids=_SESSION_IDS, scorecard_rows=[("scorecards/a.pdf", None)])

    with pytest.raises(StorageNotConfiguredError):
        await retention.purge_expired_sessions(db=db, settings=_settings())  # type: ignore[arg-type]

    assert "delete:scorecards" not in db.calls
    assert "delete:sessions" not in db.calls
    assert db.committed is False


@pytest.mark.asyncio
async def test_a_short_delete_count_aborts_before_the_row_delete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """delete_objects raises rather than under-deleting, so a shortfall means its
    contract changed. Fail loudly instead of deleting the only rows that still
    reference the surviving objects."""

    async def _short(keys_by_bucket: dict[str, list[str]], *, settings: Any) -> int:
        return 0

    monkeypatch.setattr("app.s3_upload.delete_objects", _short)
    db = _FakeDb(session_ids=_SESSION_IDS, scorecard_rows=[("scorecards/a.pdf", None)])

    with pytest.raises(RuntimeError):
        await retention.purge_expired_sessions(db=db, settings=_settings())  # type: ignore[arg-type]

    assert "delete:sessions" not in db.calls
    assert db.committed is False


# ---------------------------------------------------------------------------
# Dry run and the empty case are unchanged
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_dry_run_counts_and_touches_neither_rows_nor_storage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = False

    async def _tripwire(keys_by_bucket: dict[str, list[str]], *, settings: Any) -> int:
        nonlocal called
        called = True
        return 0

    monkeypatch.setattr("app.s3_upload.delete_objects", _tripwire)
    db = _FakeDb(session_ids=_SESSION_IDS, scorecard_rows=[("scorecards/a.pdf", None)])

    count = await retention.purge_expired_sessions(  # type: ignore[arg-type]
        db=db, settings=_settings(retention_dry_run=True)
    )

    assert count == len(_SESSION_IDS)
    assert db.calls == ["select:count"]
    assert called is False
    assert db.committed is False


@pytest.mark.asyncio
async def test_nothing_to_purge_short_circuits_before_storage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Most nights purge nothing. Calling object storage on an empty set would
    make an unconfigured deployment fail every single night for no reason."""
    called = False

    async def _tripwire(keys_by_bucket: dict[str, list[str]], *, settings: Any) -> int:
        nonlocal called
        called = True
        return 0

    monkeypatch.setattr("app.s3_upload.delete_objects", _tripwire)
    db = _FakeDb(session_ids=[], scorecard_rows=[])

    assert await retention.purge_expired_sessions(db=db, settings=_settings()) == 0  # type: ignore[arg-type]
    assert db.calls == ["select:sessions"]
    assert called is False


# ---------------------------------------------------------------------------
# The per-run cap — an unbounded run cannot finish, so it cannot comply
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_the_id_select_carries_the_per_run_limit(
    deleted_buckets: dict[str, list[str]],
) -> None:
    """``_ID_CHUNK_SIZE`` bounds the bind parameters per statement, not the run.

    Without a LIMIT on step 1 the first live run after RETENTION_DRY_RUN is
    flipped off collects the entire backlog, deletes its objects one round-trip
    at a time and holds one transaction open across all of it — and because the
    job is deliberately all-or-nothing, a single transient storage failure at any
    point discards the whole run. The cap is what makes progress monotonic.
    """
    limits: list[Any] = []

    class _RecordingDb(_FakeDb):
        async def execute(self, stmt: Any) -> Any:
            if _target_table(stmt) == "sessions" and isinstance(stmt, Select):
                limits.append(stmt._limit_clause)  # noqa: SLF001 — no public getter
            return await super().execute(stmt)

    db = _RecordingDb(session_ids=_SESSION_IDS, scorecard_rows=[])

    await retention.purge_expired_sessions(db=db, settings=_settings())  # type: ignore[arg-type]

    assert len(limits) == 1
    assert limits[0] is not None
    assert limits[0].value == retention.MAX_SESSIONS_PER_RUN


@pytest.mark.asyncio
async def test_a_full_batch_reports_capped_so_the_backlog_is_visible(
    deleted_buckets: dict[str, list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A capped run purges less than is due. That is correct — the rest goes in
    the next run — but silent truncation of a compliance job is not."""
    import structlog

    monkeypatch.setattr(retention, "MAX_SESSIONS_PER_RUN", 2)
    db = _FakeDb(session_ids=_SESSION_IDS, scorecard_rows=[])

    with structlog.testing.capture_logs() as logs:
        await retention.purge_expired_sessions(db=db, settings=_settings())  # type: ignore[arg-type]

    done = [e for e in logs if e["event"] == "retention.purge.done"]
    assert len(done) == 1
    assert done[0]["capped"] is True
    assert done[0]["max_sessions_per_run"] == 2


@pytest.mark.asyncio
async def test_a_partial_batch_is_not_reported_as_capped(
    deleted_buckets: dict[str, list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ordinary night. A ``capped`` that is true every run is not a signal."""
    import structlog

    monkeypatch.setattr(retention, "MAX_SESSIONS_PER_RUN", 50)
    db = _FakeDb(session_ids=_SESSION_IDS, scorecard_rows=[])

    with structlog.testing.capture_logs() as logs:
        await retention.purge_expired_sessions(db=db, settings=_settings())  # type: ignore[arg-type]

    done = [e for e in logs if e["event"] == "retention.purge.done"]
    assert done[0]["capped"] is False


@pytest.mark.asyncio
async def test_the_cap_does_not_weaken_the_fail_closed_storage_guarantee(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The cap changes WHICH rows a run addresses, never whether a storage
    shortfall may delete them. Same abort, on the capped batch."""

    async def _short(keys_by_bucket: dict[str, list[str]], *, settings: Any) -> int:
        return 0

    monkeypatch.setattr("app.s3_upload.delete_objects", _short)
    monkeypatch.setattr(retention, "MAX_SESSIONS_PER_RUN", 1)
    db = _FakeDb(session_ids=_SESSION_IDS, scorecard_rows=[("scorecards/a.pdf", None)])

    with pytest.raises(RuntimeError):
        await retention.purge_expired_sessions(db=db, settings=_settings())  # type: ignore[arg-type]

    assert "delete:scorecards" not in db.calls
    assert "delete:sessions" not in db.calls
    assert db.committed is False


@pytest.mark.asyncio
async def test_dry_run_counts_the_whole_backlog_and_flags_that_it_exceeds_the_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dry-run exists to tell an operator what flipping the flag will do. Capping
    its count would hide the one thing they need to know — that the backlog is
    bigger than a single run can take."""
    import structlog

    monkeypatch.setattr(retention, "MAX_SESSIONS_PER_RUN", 1)
    db = _FakeDb(session_ids=_SESSION_IDS, scorecard_rows=[])

    with structlog.testing.capture_logs() as logs:
        count = await retention.purge_expired_sessions(  # type: ignore[arg-type]
            db=db, settings=_settings(retention_dry_run=True)
        )

    assert count == len(_SESSION_IDS)  # uncapped: the true backlog size
    done = [e for e in logs if e["event"] == "retention.purge.done"]
    assert done[0]["would_exceed_cap"] is True
    assert done[0]["max_sessions_per_run"] == 1


# ---------------------------------------------------------------------------
# The storage helper's own guard
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_delete_objects_refuses_when_storage_is_unconfigured() -> None:
    """"S3 is not configured" must be a loud, retryable failure — not a silent
    no-op that lets the caller claim the objects are gone."""
    from app.s3_upload import delete_objects

    with pytest.raises(StorageNotConfiguredError):
        await delete_objects(
            {"bucket": ["some/key.pdf"]},
            settings=_settings(s3_endpoint="", s3_access_key_id=""),
        )


@pytest.mark.asyncio
async def test_delete_objects_with_no_keys_needs_no_configuration() -> None:
    from app.s3_upload import delete_objects

    result = await delete_objects(
        {"bucket": []}, settings=_settings(s3_endpoint="", s3_access_key_id="")
    )
    assert result == 0


def test_scorecards_are_named_in_the_module_inventory() -> None:
    """DPDP-4's second half: the table appeared in neither the cascade list nor
    the out-of-scope list, so the next reader could not tell it had been
    considered at all. That omission class is what the review found three times.
    """
    assert retention.__doc__ is not None
    assert "scorecards" in retention.__doc__
