"""A delivered email whose "sent" write fails must not poison the batch or be re-sent.

Before: deliver_smtp, _record_sent and commit shared one try. If the commit failed
after the mail had gone out, the except branch wrote a *failure* on the same
broken, un-rolled-back session, which raised out of ``_drain`` and aborted every
remaining row; the delivered row stayed 'sending' and the visibility timeout
re-sent it. No database: sessions and the SMTP hand-off are faked.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app import mailer


def _row(n: int) -> tuple[Any, ...]:
    return (uuid.uuid4(), f"u{n}@example.com", "subj", "<p>x</p>", "x", 1, 5, None)


class _Factory:
    """async_sessionmaker stand-in. ``plan`` decides what each commit does."""

    def __init__(self, commits: list[Any]) -> None:
        self.commits = iter(commits)
        self.executed: list[str] = []
        self.sessions = 0

    def __call__(self) -> Any:
        self.sessions += 1
        factory = self

        @asynccontextmanager
        async def _cm() -> Any:
            db = AsyncMock()

            async def _execute(stmt: Any, params: Any = None) -> None:
                factory.executed.append(str(stmt))

            async def _commit() -> None:
                effect = next(factory.commits)
                if isinstance(effect, BaseException):
                    raise effect

            db.execute = _execute
            db.commit = _commit
            yield db

        return _cm()


@pytest.fixture(autouse=True)
def _fast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mailer, "_RECORD_RETRY_PAUSE_SECONDS", 0.0)


def _batch(monkeypatch: pytest.MonkeyPatch, rows: list[Any]) -> None:
    monkeypatch.setattr(mailer, "_claim_batch", AsyncMock(return_value=rows))


async def test_a_failed_sent_write_is_retried_and_never_recorded_as_a_delivery_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = [_row(1), _row(2)]
    _batch(monkeypatch, rows)
    deliver = AsyncMock()
    monkeypatch.setattr(mailer, "deliver_smtp", deliver)
    # claim session (no commit) | row1 sent: commit fails, retry commits | row2 commits
    factory = _Factory([RuntimeError("conn reset"), None, None])

    assert await mailer._drain(factory) == 2  # type: ignore[arg-type]

    assert deliver.await_count == 2  # both rows were attempted
    assert not any("status=:st" in e for e in factory.executed)  # no failure written
    assert sum("status='sent'" in e for e in factory.executed) == 3  # row1 twice, row2 once


async def test_unrecordable_delivery_does_not_abort_the_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = [_row(1), _row(2)]
    _batch(monkeypatch, rows)
    deliver = AsyncMock()
    monkeypatch.setattr(mailer, "deliver_smtp", deliver)
    err = MagicMock()
    monkeypatch.setattr(mailer.log, "error", err)
    # row1: all three recordings fail; row2: records fine
    factory = _Factory([RuntimeError("down")] * 3 + [None])

    assert await mailer._drain(factory) == 2  # type: ignore[arg-type]

    assert deliver.await_count == 2
    assert any(c.args[0] == "email.delivered_unrecorded" for c in err.call_args_list)


async def test_a_failed_delivery_is_recorded_as_a_failure_and_the_batch_continues(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = [_row(1), _row(2)]
    _batch(monkeypatch, rows)
    deliver = AsyncMock(side_effect=[OSError("smtp refused"), None])
    monkeypatch.setattr(mailer, "deliver_smtp", deliver)
    factory = _Factory([None, None])

    assert await mailer._drain(factory) == 2  # type: ignore[arg-type]

    assert sum("status=:st" in e for e in factory.executed) == 1  # row1's failure
    assert sum("status='sent'" in e for e in factory.executed) == 1  # row2's delivery
