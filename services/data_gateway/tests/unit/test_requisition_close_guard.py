"""AC-12 — closing an opening cannot silently strand the people in it.

The sign-off's wording is "a requisition cannot be closed while candidates
remain unresolved; closing prompts a decision on each". The endpoint previously
returned 200 with an ``unresolved`` count in the body, and nothing in the
console read it — so an opening closed and its candidates were stranded with
nobody prompted, which is precisely the failure AC-12 describes.

Closing is now refused with 409 while anyone is mid-process, and the refusal
carries the count so the caller can prompt. Nobody is rejected on either path:
under D-05 a closed opening's candidates stay exactly as they are.

DB is mocked, matching the house pattern in ``test_hr_rounds.py`` — the
behaviour under test is the decision to refuse, not the SQL.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

_COMPANY = uuid.uuid4()
_REQ = uuid.uuid4()
_NOW = datetime(2026, 9, 7, 12, 0, tzinfo=UTC)


def _ctx() -> tuple[uuid.UUID, uuid.UUID]:
    return uuid.uuid4(), _COMPANY


def _row(status: str = "open") -> dict[str, Any]:
    """A job_requisitions row as ``_owned`` returns it."""
    return {
        "id": _REQ,
        "company_id": _COMPANY,
        "title": "Backend Engineer",
        "level": "mid",
        "status": status,
        "jd_text": "",
        "target_hires": 1,
        "closes_at": None,
        "owner_user_id": None,
        "from_backfill": False,
        "created_at": _NOW,
        "updated_at": _NOW,
        "public_apply_enabled": False,
        "public_slug": None,
        "department": None,
        "location": None,
        "employment_type": None,
        "experience_min_years": None,
        "experience_max_years": None,
        "salary_min": None,
        "salary_max": None,
        "salary_currency": None,
        "salary_visible": False,
        "responsibilities": [],
        "required_skills": [],
        "nice_to_have_skills": [],
    }


def _db(counts: dict[str, int]) -> AsyncMock:
    """A db whose only read is ``_counts``' status histogram."""
    db = AsyncMock()
    rows = [
        {"requisition_id": _REQ, "status": k, "n": v} for k, v in counts.items()
    ]
    result = MagicMock()
    result.mappings.return_value.all.return_value = rows
    db.execute = AsyncMock(return_value=result)
    db.commit = AsyncMock()
    db.add = MagicMock()
    return db


def _request() -> Any:
    req = MagicMock()
    req.client.host = "203.0.113.9"
    req.headers = {}
    return req


async def _close(
    db: AsyncMock, row: dict[str, Any], body: dict[str, Any]
) -> Any:
    from app.routers import hr_requisitions as mod

    async def _owned(*_a: object, **_k: object) -> dict[str, Any]:
        return row

    orig = mod._owned
    mod._owned = _owned  # type: ignore[assignment]
    try:
        return await mod.set_requisition_status(
            _REQ, body, _request(), _ctx(), db
        )
    finally:
        mod._owned = orig  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# The refusal
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_close_refused_while_candidates_are_unresolved() -> None:
    db = _db({"shortlisted": 2, "interviewing": 1, "hired": 4})

    with pytest.raises(HTTPException) as exc:
        await _close(db, _row("open"), {"status": "closed"})

    assert exc.value.status_code == 409
    detail = exc.value.detail
    assert isinstance(detail, dict)
    assert detail["error"] == "unresolved_candidates"
    # hired is terminal; the other three are not.
    assert detail["unresolved"] == 3
    assert str(_REQ) in detail["decision_queue"]
    # Nothing was written.
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_refusal_names_a_count_the_caller_can_show() -> None:
    """The message has to carry the number, not just a status code.

    The console renders this when it cannot map the shape itself, and "some
    candidates are unresolved" is not something an HR manager can act on.
    """
    db = _db({"applied": 7})

    with pytest.raises(HTTPException) as exc:
        await _close(db, _row("open"), {"status": "closed"})

    assert "7 candidate" in exc.value.detail["message"]


# ---------------------------------------------------------------------------
# The acknowledged path
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_close_proceeds_when_acknowledged() -> None:
    db = _db({"shortlisted": 2})

    out = await _close(
        db, _row("open"), {"status": "closed", "acknowledge_unresolved": True}
    )

    assert out.status == "closed"
    db.commit.assert_awaited()


@pytest.mark.asyncio
async def test_acknowledgement_accepts_the_json_string_form() -> None:
    """An operator curling this sends "true", not a JSON boolean."""
    db = _db({"shortlisted": 1})

    out = await _close(
        db, _row("open"), {"status": "closed", "acknowledge_unresolved": "true"}
    )

    assert out.status == "closed"


@pytest.mark.asyncio
async def test_acknowledged_close_is_recorded_in_the_audit_log() -> None:
    """The compliance half: the trail must show a person chose this.

    Without the flag an audit cannot tell an acknowledged close apart from
    closing an opening that was already settled.
    """
    db = _db({"shortlisted": 3})

    await _close(
        db, _row("open"), {"status": "closed", "acknowledge_unresolved": True}
    )

    entry = db.add.call_args[0][0]
    assert entry.action == "requisition.status.closed"
    assert entry.details["closed_with_unresolved"] == 3


# ---------------------------------------------------------------------------
# Cases that must NOT prompt
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_close_with_everyone_settled_does_not_prompt() -> None:
    db = _db({"hired": 2, "rejected": 5})

    out = await _close(db, _row("open"), {"status": "closed"})

    assert out.status == "closed"
    # No acknowledgement was needed, so nothing is recorded as overridden.
    assert "closed_with_unresolved" not in db.add.call_args[0][0].details


@pytest.mark.asyncio
async def test_reclosing_a_closed_opening_does_not_prompt() -> None:
    """Re-closing is a no-op and must not re-ask."""
    db = _db({"shortlisted": 4})

    out = await _close(db, _row("closed"), {"status": "closed"})

    assert out.status == "closed"


@pytest.mark.asyncio
async def test_pausing_never_prompts() -> None:
    """Only closing can strand anyone; pause is reversible and silent."""
    db = _db({"shortlisted": 9})

    out = await _close(db, _row("open"), {"status": "paused"})

    assert out.status == "paused"


@pytest.mark.asyncio
async def test_invalid_status_still_rejected_first() -> None:
    db = _db({"shortlisted": 1})

    with pytest.raises(HTTPException) as exc:
        await _close(db, _row("open"), {"status": "banana"})

    assert exc.value.status_code == 400
