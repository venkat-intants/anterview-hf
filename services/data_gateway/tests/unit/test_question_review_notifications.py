"""A question sent for review alerts the company's super admins (audit AD-F18).

Before this, an opening waiting for approval and a question waiting for review
were both silent: the super admin found them only by opening the console. The
opening half was fixed in requisition_approval; these tests pin the question
half, on both submit routes, and that a bulk submit is one alert, not hundreds.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app import question_banks as svc
from app.routers import question_banks as routes

COMPANY = uuid.UUID("22222222-2222-2222-2222-222222222222")
BANK = uuid.UUID("55555555-5555-5555-5555-555555555555")
HR = uuid.UUID("33333333-3333-3333-3333-333333333333")
SA1 = uuid.UUID("44444444-4444-4444-4444-444444444441")
SA2 = uuid.UUID("44444444-4444-4444-4444-444444444442")


def _capture(monkeypatch: pytest.MonkeyPatch, *, admins: list[uuid.UUID]) -> list[dict[str, Any]]:
    sent: list[dict[str, Any]] = []

    async def _notify(_db: object, **kw: Any) -> bool:
        sent.append(kw)
        return True

    async def _admins(_db: object, *, company_id: uuid.UUID) -> list[uuid.UUID]:
        assert company_id == COMPANY, "reviewers must come from the caller's company"
        return admins

    monkeypatch.setattr(svc, "create_notification", _notify)
    monkeypatch.setattr(svc, "company_super_admin_ids", _admins)
    return sent


def _db(bank_name: str | None = "Backend basics") -> MagicMock:
    db = MagicMock()
    db.scalar = AsyncMock(return_value=bank_name)
    return db


@pytest.mark.asyncio
async def test_every_super_admin_is_told_and_linked_to_the_queue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent = _capture(monkeypatch, admins=[SA1, SA2])
    told = await svc.notify_reviewers(_db(), company_id=COMPANY, bank_id=BANK, count=1)
    assert told == 2
    assert [n["user_id"] for n in sent] == [SA1, SA2]
    assert {n["link"] for n in sent} == {"/superadmin/question-reviews"}
    assert sent[0]["title"] == "Backend basics: a question needs review"


@pytest.mark.asyncio
async def test_a_bulk_submit_is_one_alert_with_the_count(monkeypatch: pytest.MonkeyPatch) -> None:
    sent = _capture(monkeypatch, admins=[SA1])
    await svc.notify_reviewers(_db(), company_id=COMPANY, bank_id=BANK, count=40)
    assert len(sent) == 1
    assert sent[0]["title"] == "Backend basics: 40 questions need review"


@pytest.mark.asyncio
async def test_nothing_submitted_means_nobody_is_told(monkeypatch: pytest.MonkeyPatch) -> None:
    sent = _capture(monkeypatch, admins=[SA1])
    assert await svc.notify_reviewers(_db(), company_id=COMPANY, bank_id=BANK, count=0) == 0
    assert sent == []


@pytest.mark.asyncio
async def test_submitting_one_question_notifies_before_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The alert is staged on the submit's own transaction: it is written by
    the same commit, so a refused commit leaves no stray notification."""
    sent = _capture(monkeypatch, admins=[SA1])
    order: list[str] = []
    question = SimpleNamespace(id=uuid.uuid4(), bank_id=BANK)

    async def _submit(_db: object, **_kw: object) -> object:
        return question

    async def _commit(_db: object) -> None:
        order.append(f"commit after {len(sent)} notification(s)")

    monkeypatch.setattr(svc, "submit", _submit)
    monkeypatch.setattr(svc, "question_out", lambda q: {"id": str(q.id)})
    monkeypatch.setattr(routes.exam_locks, "commit_or_conflict", _commit)

    await routes.submit_bank_question(question.id, (HR, COMPANY), _db())
    assert [n["user_id"] for n in sent] == [SA1]
    assert order == ["commit after 1 notification(s)"]


@pytest.mark.asyncio
async def test_submit_all_notifies_once_with_what_was_submitted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent = _capture(monkeypatch, admins=[SA1, SA2])

    async def _submit_all(_db: object, **_kw: object) -> dict[str, Any]:
        return {"submitted": 3, "skipped": []}

    async def _commit(_db: object) -> None:
        return None

    monkeypatch.setattr(svc, "submit_all", _submit_all)
    monkeypatch.setattr(routes.exam_locks, "commit_or_conflict", _commit)

    out = await routes.submit_all_bank_questions(BANK, (HR, COMPANY), _db())
    assert out.acted == 3
    assert len(sent) == 2
    assert all(n["title"].endswith("3 questions need review") for n in sent)


@pytest.mark.asyncio
async def test_a_refused_submit_notifies_nobody(monkeypatch: pytest.MonkeyPatch) -> None:
    sent = _capture(monkeypatch, admins=[SA1])

    async def _submit(_db: object, **_kw: object) -> object:
        raise svc.QuestionBankError(409, "Only a draft can be submitted for review.")

    async def _fail(_db: object, exc: svc.QuestionBankError) -> Exception:
        return RuntimeError(exc.detail)

    monkeypatch.setattr(svc, "submit", _submit)
    monkeypatch.setattr(routes, "_fail", _fail)

    with pytest.raises(RuntimeError):
        await routes.submit_bank_question(uuid.uuid4(), (HR, COMPANY), _db())
    assert sent == []
