"""DPDP-6 — a withdrawal is now distinguishable from an abandonment.

``consent_withdrawn`` has been in the session status vocabulary since the
retention job was written and NO code path wrote it, so a candidate who exercised
DPDP §6(4) mid-interview landed in the audit trail under a generic terminal
status — indistinguishable from one who closed the tab. That is precisely the
distinction §6(4) exists to make auditable, and precisely what a regulator asks
to see.

The status is also load-bearing downstream: ``retention.py`` purges it at the
next nightly window instead of waiting out the 90-day retention window, so
writing it is what actually causes the withdrawn candidate's data to leave.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from sqlalchemy import Update

from app.models import DpdpConsent
from app.retention import CONSENT_WITHDRAWN_STATUS
from app.routers import consent as consent_router

_USER_ID = str(uuid.uuid4())


class _FakeDb:
    """Answers the two selects the revoke path makes, and records the UPDATE."""

    def __init__(self, *, active_types: set[str], sessions_updated: int) -> None:
        self.active_types = active_types
        self.sessions_updated = sessions_updated
        self.updates: list[Update] = []
        self.committed = False

    async def execute(self, stmt: Any) -> Any:
        result = MagicMock()
        if isinstance(stmt, Update):
            self.updates.append(stmt)
            result.rowcount = self.sessions_updated
            return result
        # A _find_active_consent select. Which type it asks about is carried in
        # the compiled parameters, so answer per type rather than unconditionally.
        params = stmt.compile().params
        wanted = params.get("consent_type_1")
        if wanted in self.active_types:
            result.scalar_one_or_none.return_value = DpdpConsent(
                id=uuid.uuid4(),
                user_id=uuid.UUID(_USER_ID),
                consent_type=wanted,
                granted=True,
                granted_at=datetime.now(UTC),
                revoked_at=None,
                purpose="interview",
                evidence={},
            )
        else:
            result.scalar_one_or_none.return_value = None
        return result

    async def commit(self) -> None:
        self.committed = True


def _user() -> Any:
    return SimpleNamespace(user_id=_USER_ID)


def _update_sql(stmt: Update) -> str:
    return str(stmt.compile(compile_kwargs={"literal_binds": True}))


# ---------------------------------------------------------------------------
# The write itself
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_revoking_consent_stamps_in_flight_sessions() -> None:
    db = _FakeDb(active_types={"interview_voice_recording"}, sessions_updated=1)

    resp = await consent_router.revoke_consent(current_user=_user(), db=db)  # type: ignore[arg-type]

    assert resp.revoked is True
    assert len(db.updates) == 1
    sql = _update_sql(db.updates[0])
    assert "UPDATE sessions" in sql
    assert CONSENT_WITHDRAWN_STATUS in sql


@pytest.mark.asyncio
async def test_only_non_terminal_sessions_are_rewritten() -> None:
    """A completed or failed session's status records what happened to the
    interview. Rewriting it would falsify the history the audit trail exists for
    — and would make an already-scored session look like a withdrawal."""
    db = _FakeDb(active_types={"interview_voice_recording"}, sessions_updated=0)

    await consent_router.revoke_consent(current_user=_user(), db=db)  # type: ignore[arg-type]

    sql = _update_sql(db.updates[0])
    assert "'created'" in sql
    assert "'in_progress'" in sql
    assert "'completed'" not in sql
    assert "'abandoned'" not in sql


@pytest.mark.asyncio
async def test_the_status_write_shares_the_revocation_transaction() -> None:
    """A crash between the two would leave a revoked consent whose sessions still
    read 'in_progress' — the exact ambiguity this write removes."""
    db = _FakeDb(active_types={"video_capture"}, sessions_updated=2)

    await consent_router.revoke_consent(current_user=_user(), db=db)  # type: ignore[arg-type]

    assert db.updates, "the session UPDATE must be staged before the commit"
    assert db.committed is True


@pytest.mark.asyncio
async def test_nothing_to_revoke_writes_no_status() -> None:
    """404 means no consent was withdrawn, so no session ended because of one."""
    from fastapi import HTTPException

    db = _FakeDb(active_types=set(), sessions_updated=0)

    with pytest.raises(HTTPException) as exc:
        await consent_router.revoke_consent(current_user=_user(), db=db)  # type: ignore[arg-type]

    assert exc.value.status_code == 404
    assert db.updates == []
    assert db.committed is False


def test_the_status_constant_has_one_definition() -> None:
    """The writer (this router) and the retention vocabulary must not drift into
    two spellings of the same status — that is how the status came to exist in
    one place and be written in none."""
    from app import retention

    assert CONSENT_WITHDRAWN_STATUS in retention._PURGEABLE_STATUSES
    assert consent_router.CONSENT_WITHDRAWN_STATUS is retention.CONSENT_WITHDRAWN_STATUS
