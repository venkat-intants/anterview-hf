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
from sqlalchemy.sql.elements import TextClause

from app.models import DpdpConsent
from app.retention import CONSENT_WITHDRAWN_STATUS
from app.routers import consent as consent_router

_USER_ID = str(uuid.uuid4())


class _FakeDb:
    """Answers the selects the revoke path makes, and records the UPDATE.

    PH5-E3 added a third statement shape: a raw ``text()`` UPDATE ... RETURNING
    that revokes every company's talent-pool rediscovery opt-in in one go
    (``app/rediscovery.py::revoke_opt_ins``), called WITH bound parameters —
    which is why ``execute`` now takes them. It is answered from
    ``rediscovery_rows`` (a list of ``(consent_id, company_id)`` pairs) so a test
    can decide whether this person had any.
    """

    def __init__(
        self,
        *,
        active_types: set[str],
        sessions_updated: int,
        rediscovery_rows: list[tuple[str, str]] | None = None,
    ) -> None:
        self.active_types = active_types
        self.sessions_updated = sessions_updated
        self.rediscovery_rows = rediscovery_rows or []
        self.updates: list[Update] = []
        self.rediscovery_statements: list[str] = []
        self.committed = False

    async def execute(self, stmt: Any, params: Any = None) -> Any:
        result = MagicMock()
        if isinstance(stmt, Update):
            self.updates.append(stmt)
            result.rowcount = self.sessions_updated
            return result
        if isinstance(stmt, TextClause):
            self.rediscovery_statements.append(str(stmt))
            now = datetime.now(UTC)
            result.mappings.return_value.all.return_value = [
                {"id": consent_id, "company_id": company_id, "revoked_at": now}
                for consent_id, company_id in self.rediscovery_rows
            ]
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

    resp = await consent_router.revoke_consent(current_user=_user(), db=db, request=MagicMock())  # type: ignore[arg-type]

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

    await consent_router.revoke_consent(current_user=_user(), db=db, request=MagicMock())  # type: ignore[arg-type]

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

    await consent_router.revoke_consent(current_user=_user(), db=db, request=MagicMock())  # type: ignore[arg-type]

    assert db.updates, "the session UPDATE must be staged before the commit"
    assert db.committed is True


def _request() -> Any:
    return SimpleNamespace(headers={}, client=SimpleNamespace(host="127.0.0.1"))


@pytest.mark.asyncio
async def test_a_documents_consent_revoked_here_tells_the_hiring_team(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Once this route could actually FIND the documents consent (4802bb3),
    revoking it here owed what the documents step's own withdrawal does: an
    audit row and a notice to the hiring team. It skipped both."""
    import app.preboarding as preboarding

    calls: list[dict[str, Any]] = []

    async def _record(_db: Any, **kw: Any) -> None:
        calls.append(kw)

    monkeypatch.setattr(preboarding, "documents_consent_withdrawn_elsewhere", _record)
    db = _FakeDb(active_types={"preboarding_documents"}, sessions_updated=0)

    resp = await consent_router.revoke_consent(current_user=_user(), db=db, request=_request())  # type: ignore[arg-type]

    assert resp.revoked is True
    assert len(calls) == 1
    assert str(calls[0]["user_id"]) == _USER_ID


@pytest.mark.asyncio
async def test_revoking_only_interview_consents_tells_no_hiring_team(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.preboarding as preboarding

    calls: list[dict[str, Any]] = []

    async def _record(_db: Any, **kw: Any) -> None:
        calls.append(kw)

    monkeypatch.setattr(preboarding, "documents_consent_withdrawn_elsewhere", _record)
    db = _FakeDb(active_types={"interview_voice_recording"}, sessions_updated=0)

    await consent_router.revoke_consent(current_user=_user(), db=db, request=_request())  # type: ignore[arg-type]

    assert calls == []


@pytest.mark.asyncio
async def test_withdrawal_revokes_every_companys_rediscovery_opt_in() -> None:
    """PH5-E3. DPDP §11 is "at any time WITHOUT RESTRICTION", and a rediscovery
    opt-in is one row per COMPANY — so a global withdrawal has to take all of
    them, in one statement, not "the" active row. The response names each one, so
    the candidate is told what was turned off rather than just that something
    was."""
    rows = [(str(uuid.uuid4()), str(uuid.uuid4())), (str(uuid.uuid4()), str(uuid.uuid4()))]
    db = _FakeDb(active_types=set(), sessions_updated=0, rediscovery_rows=rows)

    resp = await consent_router.revoke_consent(current_user=_user(), db=db, request=MagicMock())  # type: ignore[arg-type]

    assert resp.revoked is True
    revoked = [i for i in resp.items if i.consent_type == "talent_pool_rediscovery"]
    assert len(revoked) == 2
    assert {i.consent_id for i in revoked} == {consent_id for consent_id, _ in rows}
    # One statement, no company filter — that is what "every company" means here.
    assert len(db.rediscovery_statements) == 1
    assert "evidence ->> 'company_id' = :cid" not in db.rediscovery_statements[0]
    assert db.committed is True


@pytest.mark.asyncio
async def test_a_rediscovery_opt_in_alone_is_enough_to_not_404() -> None:
    """Before E3 this route had no notion of the type, so a candidate whose ONLY
    live consent was a rediscovery opt-in got a 404 from the route that is
    supposed to be their withdrawal door."""
    db = _FakeDb(
        active_types=set(), sessions_updated=0,
        rediscovery_rows=[(str(uuid.uuid4()), str(uuid.uuid4()))],
    )
    resp = await consent_router.revoke_consent(current_user=_user(), db=db, request=MagicMock())  # type: ignore[arg-type]
    assert [i.consent_type for i in resp.items] == ["talent_pool_rediscovery"]


@pytest.mark.asyncio
async def test_nothing_to_revoke_writes_no_status() -> None:
    """404 means no consent was withdrawn, so no session ended because of one."""
    from fastapi import HTTPException

    db = _FakeDb(active_types=set(), sessions_updated=0)

    with pytest.raises(HTTPException) as exc:
        await consent_router.revoke_consent(current_user=_user(), db=db, request=MagicMock())  # type: ignore[arg-type]

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
