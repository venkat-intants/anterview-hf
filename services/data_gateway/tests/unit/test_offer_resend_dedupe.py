"""Two resends in the same second must each send their own email.

The dedupe key used to end in a one-second timestamp, so the second resend
collided with the first (email_events.dedupe_key is UNIQUE) and no email was sent
for the token it had rotated in, leaving the candidate holding a dead link.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app import offers


def _offer() -> dict[str, Any]:
    return {
        "id": uuid.uuid4(), "status": "sent", "candidate_email": "c@example.com",
        "preboarding_completed_at": None, "expires_at": __import__("datetime").datetime(
            2030, 1, 1, tzinfo=__import__("datetime").UTC),
        "company_name": "Acme", "valid_days": 7,
    }


async def _resend(monkeypatch: pytest.MonkeyPatch, offer: dict[str, Any]) -> str:
    keys: list[str] = []

    async def fake_email(db: Any, off: Any, template: str, ctx: Any, key: str) -> None:
        keys.append(key)

    monkeypatch.setattr(offers, "_load", AsyncMock(return_value=offer))
    monkeypatch.setattr(offers, "_record", AsyncMock())
    monkeypatch.setattr(offers, "_email_candidate", fake_email)
    monkeypatch.setattr(offers, "offer_out", lambda o: o)
    monkeypatch.setattr(offers, "hash_offer_token", lambda raw: "h" + raw * 2)
    db = MagicMock()
    db.execute = AsyncMock()
    await offers.send(
        db, company_id=uuid.uuid4(), offer_id=offer["id"], actor=uuid.uuid4(),
        meta=SimpleNamespace(), again=True,  # type: ignore[arg-type]
    )
    return keys[0]


async def test_resend_keys_differ_even_in_the_same_instant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    offer = _offer()
    frozen = __import__("datetime").datetime(2026, 1, 1, 12, 0, 0, tzinfo=__import__("datetime").UTC)

    class _Frozen(__import__("datetime").datetime):
        @classmethod
        def now(cls, tz: Any = None) -> Any:
            return frozen

    monkeypatch.setattr(offers, "datetime", _Frozen)
    first = await _resend(monkeypatch, offer)
    second = await _resend(monkeypatch, offer)  # new mint_offer_token each call

    assert first != second
    assert first.startswith(f"offer:{offer['id']}:resent:")
    assert "20260101120000" not in first  # no timestamp in the key


async def test_the_first_send_key_is_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only resends changed: the initial send is still one email per offer."""
    import inspect

    src = inspect.getsource(offers.send)
    assert "'sent'" in src and "offer:{offer_id}:" in src


# ---------------------------------------------------------------------------
# The candidate's in-app notification
# ---------------------------------------------------------------------------
class _Savepoint:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *exc: Any) -> bool:
        return False  # never swallow: the code under test must handle errors itself


def _db() -> MagicMock:
    db = MagicMock()
    db.execute = AsyncMock()
    db.begin_nested = MagicMock(side_effect=lambda: _Savepoint())
    return db


def _patch_send(monkeypatch: pytest.MonkeyPatch, offer: dict[str, Any]) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    async def fake_notify(db: Any, **kw: Any) -> bool:
        calls.append(kw)
        return True

    monkeypatch.setattr(offers, "create_notification", fake_notify)
    monkeypatch.setattr(offers, "_load", AsyncMock(return_value=offer))
    monkeypatch.setattr(offers, "_record", AsyncMock())
    monkeypatch.setattr(offers, "_email_candidate", AsyncMock())
    monkeypatch.setattr(offers, "offer_out", lambda o: o)
    return calls


async def _send(db: Any, offer: dict[str, Any], again: bool = False) -> None:
    await offers.send(
        db, company_id=uuid.uuid4(), offer_id=offer["id"], actor=uuid.uuid4(),
        meta=SimpleNamespace(), again=again,  # type: ignore[arg-type]
    )


def _approved(user_id: uuid.UUID | None) -> dict[str, Any]:
    return {**_offer(), "status": "approved", "candidate_user_id": user_id,
            "job_title": "Welder", "candidate_name": "Asha",
            "salary": "1200000", "base_amount": "1200000"}


async def test_send_notifies_only_the_candidates_own_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    uid = uuid.uuid4()
    offer = _approved(uid)
    calls = _patch_send(monkeypatch, offer)
    await _send(_db(), offer)

    assert len(calls) == 1
    n = calls[0]
    assert n["user_id"] == uid
    assert n["kind"] == "candidate_offer"
    assert n["title"] == "You have an offer"
    assert n["link"] == "/applications"
    assert n["body"] == "Welder"
    assert n["dedupe_key"] == f"offer:{offer['id']}:sent"


async def test_resend_says_updated(monkeypatch: pytest.MonkeyPatch) -> None:
    offer = {**_approved(uuid.uuid4()), "status": "sent"}
    calls = _patch_send(monkeypatch, offer)
    await _send(_db(), offer, again=True)
    assert [c["title"] for c in calls] == ["Your offer was updated"]
    assert ":resent:" in calls[0]["dedupe_key"]


async def test_no_account_means_no_notification(monkeypatch: pytest.MonkeyPatch) -> None:
    offer = _approved(None)
    calls = _patch_send(monkeypatch, offer)
    db = _db()
    await _send(db, offer)
    assert calls == []
    db.begin_nested.assert_not_called()


async def test_text_carries_no_pay_and_no_token(monkeypatch: pytest.MonkeyPatch) -> None:
    offer = _approved(uuid.uuid4())
    calls = _patch_send(monkeypatch, offer)
    await _send(_db(), offer)
    shown = f"{calls[0]['title']} {calls[0]['body']} {calls[0]['link']}"
    assert "1200000" not in shown and "#" not in shown and "http" not in shown
    assert not any(ch.isdigit() for ch in shown)


async def test_a_failing_notification_does_not_fail_the_send(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    offer = _approved(uuid.uuid4())
    _patch_send(monkeypatch, offer)
    monkeypatch.setattr(offers, "create_notification",
                        AsyncMock(side_effect=RuntimeError("bell down")))
    result = await offers.send(
        _db(), company_id=uuid.uuid4(), offer_id=offer["id"], actor=uuid.uuid4(),
        meta=SimpleNamespace(),  # type: ignore[arg-type]
    )
    assert result is offer  # the send completed and returned its offer


async def test_withdraw_and_expiry_tell_the_candidate(monkeypatch: pytest.MonkeyPatch) -> None:
    uid = uuid.uuid4()
    offer = {**_approved(uid), "status": "sent", "enrolment_id": uuid.uuid4(),
             "created_by_user_id": None, "sent_by_user_id": None}
    calls = _patch_send(monkeypatch, offer)
    monkeypatch.setattr(offers, "_set_outcome", AsyncMock())
    await offers.withdraw(_db(), company_id=uuid.uuid4(), offer_id=offer["id"], reason=None,
                          actor=uuid.uuid4(), meta=SimpleNamespace())  # type: ignore[arg-type]
    assert [(c["user_id"], c["title"]) for c in calls] == [(uid, "Your offer was withdrawn")]

    calls.clear()
    db = _db()
    db.scalar = AsyncMock(return_value=offer["id"])
    await offers._expire(db, offer)
    # HR's existing notice is unchanged; the candidate's is added after it.
    assert [(c["kind"], c["user_id"]) for c in calls] == [("candidate_offer", uid)]
    assert calls[0]["title"] == "Your offer has expired"


async def test_hr_notice_is_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    hr = uuid.uuid4()
    offer = {**_approved(uuid.uuid4()), "created_by_user_id": hr, "sent_by_user_id": None}
    calls = _patch_send(monkeypatch, offer)
    await offers._tell_hr(_db(), offer, "Offer accepted")
    assert calls == [{"user_id": hr, "kind": "offer_update", "title": "Offer accepted: Asha",
                      # The offer itself, not the list (audit OF-L10).
                      "body": "Welder", "link": f"/hr/offers/{offer['id']}"}]
