"""Unit tests for applicant account activation.

DB is mocked, matching the house pattern: what is under test is the decision
logic — is this account already claimed, is the token usable, and which of the
two branches (promote vs link) should run. The SQL is exercised end to end by
the bridge walk in ``tests/integration/smoke_apply_activation.py``.

The tests worth reading twice are the ones about which branch runs. Picking
wrong is not a cosmetic bug: promoting when an account already exists violates
``uq_users_email`` and 500s the request, and linking when one does not exist
would attach the application to nobody.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest


def _row(**kw: object) -> MagicMock:
    row = MagicMock()
    for k, v in kw.items():
        setattr(row, k, v)
    return row


def _result(first: object = None, all_: list | None = None) -> MagicMock:
    res = MagicMock()
    res.first = MagicMock(return_value=first)
    res.all = MagicMock(return_value=all_ or [])
    return res


def _db(*, executes: list | None = None, scalar: object = None) -> AsyncMock:
    """A session whose execute() returns the queued results in order."""
    db = AsyncMock()
    queue = list(executes or [])

    async def _execute(*_a: object, **_k: object) -> MagicMock:
        return queue.pop(0) if queue else _result()

    db.execute = AsyncMock(side_effect=_execute)
    db.scalar = AsyncMock(return_value=scalar)
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    return db


# ===========================================================================
# _is_claimed — decides whether an activation link is even offered
# ===========================================================================
@pytest.mark.asyncio
async def test_placeholder_row_is_not_claimed() -> None:
    """The state every fresh applicant is in: placeholder address, no password."""
    from app.apply_activation import _is_claimed

    db = _db(executes=[_result(_row(email="guest+abc@applicants.invalid", password_hash=None))])
    assert await _is_claimed(db, uuid.uuid4()) is False


@pytest.mark.asyncio
async def test_real_address_counts_as_claimed() -> None:
    from app.apply_activation import _is_claimed

    db = _db(executes=[_result(_row(email="asha@example.com", password_hash=None))])
    assert await _is_claimed(db, uuid.uuid4()) is True


@pytest.mark.asyncio
async def test_placeholder_with_a_password_counts_as_claimed() -> None:
    """Belt and braces: a password on the row means somebody set one."""
    from app.apply_activation import _is_claimed

    db = _db(executes=[_result(_row(email="guest+x@applicants.invalid", password_hash="$2b$12$x"))])
    assert await _is_claimed(db, uuid.uuid4()) is True


@pytest.mark.asyncio
async def test_missing_user_is_not_claimed() -> None:
    from app.apply_activation import _is_claimed

    assert await _is_claimed(_db(executes=[_result(None)]), uuid.uuid4()) is False


# ===========================================================================
# _live_token_user — one message for every unusable token
# ===========================================================================
@pytest.mark.asyncio
async def test_live_token_returns_its_user() -> None:
    from app.apply_activation import _live_token_user

    uid = uuid.uuid4()
    db = _db(
        executes=[
            _result(
                _row(
                    user_id=str(uid),
                    consumed_at=None,
                    expires_at=datetime.now(tz=UTC) + timedelta(days=3),
                )
            )
        ]
    )
    assert await _live_token_user(db, "raw") == uid


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "row",
    [
        None,
        _row(
            user_id=str(uuid.uuid4()),
            consumed_at=datetime.now(tz=UTC),
            expires_at=datetime.now(tz=UTC) + timedelta(days=3),
        ),
        _row(
            user_id=str(uuid.uuid4()),
            consumed_at=None,
            expires_at=datetime.now(tz=UTC) - timedelta(seconds=1),
        ),
    ],
    ids=["never-existed", "already-used", "expired"],
)
async def test_unusable_tokens_are_indistinguishable(row: object) -> None:
    """A used link must not be tellable from one that was never issued."""
    from app.apply_activation import ActivationError, _live_token_user

    db = _db(executes=[_result(row)])
    with pytest.raises(ActivationError) as exc:
        await _live_token_user(db, "raw")
    assert "invalid or has expired" in str(exc.value)


# ===========================================================================
# activate — branch selection
# ===========================================================================
def _token_result() -> MagicMock:
    return _result(
        _row(
            user_id=str(uuid.uuid4()),
            consumed_at=None,
            expires_at=datetime.now(tz=UTC) + timedelta(days=3),
        )
    )


@pytest.mark.asyncio
async def test_activate_promotes_when_no_account_exists(monkeypatch: pytest.MonkeyPatch) -> None:
    from app import apply_activation

    called: list[str] = []
    monkeypatch.setattr(
        apply_activation, "_promote_guest",
        AsyncMock(side_effect=lambda *a, **k: called.append("promote")),
    )
    monkeypatch.setattr(
        apply_activation, "_link_to_existing",
        AsyncMock(side_effect=lambda *a, **k: called.append("link")),
    )

    db = _db(
        executes=[
            _token_result(),
            _result(_row(id=uuid.uuid4(), email="Asha@Example.com", full_name="Asha")),
            _result(_row(id=uuid.uuid4())),  # _consume: the conditional UPDATE won
        ],
        scalar=None,  # no existing user with that address
    )
    out = await apply_activation.activate(db, raw_token="raw", new_password="GoodPass123!")
    assert called == ["promote"]
    assert out["linked"] == "new"
    # Lower-cased: the address becomes a login identity, and two rows differing
    # only by case would be two accounts for one person.
    assert out["email"] == "asha@example.com"


@pytest.mark.asyncio
async def test_activate_links_when_the_address_already_has_an_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app import apply_activation

    called: list[str] = []
    monkeypatch.setattr(
        apply_activation, "_promote_guest",
        AsyncMock(side_effect=lambda *a, **k: called.append("promote")),
    )
    monkeypatch.setattr(
        apply_activation, "_link_to_existing",
        AsyncMock(side_effect=lambda *a, **k: called.append("link")),
    )

    db = _db(
        executes=[
            _token_result(),
            _result(_row(id=uuid.uuid4(), email="ravi@example.com", full_name="Ravi")),
            _result(_row(id=uuid.uuid4())),  # _consume: the conditional UPDATE won
            _result(),  # _consume: sweep of the user's other outstanding tokens
            # _is_takeover_target: a VERIFIED account is linked, never rewritten
            _result(_row(password_hash="$2b$x", email_verified_at=datetime.now(tz=UTC), roles=[])),
        ],
        scalar=str(uuid.uuid4()),  # an account already holds this address
    )
    out = await apply_activation.activate(db, raw_token="raw", new_password="Ignored123!")
    assert called == ["link"]
    assert out["linked"] == "existing"


@pytest.mark.asyncio
async def test_activate_refuses_when_the_applicant_row_is_gone() -> None:
    """Erasure between email and click. Better a clear refusal than a crash."""
    from app.apply_activation import ActivationError, activate

    db = _db(executes=[_token_result(), _result(None)])
    with pytest.raises(ActivationError):
        await activate(db, raw_token="raw", new_password="GoodPass123!")


@pytest.mark.asyncio
async def test_activate_refuses_an_applicant_with_no_email() -> None:
    from app.apply_activation import ActivationError, activate

    db = _db(
        executes=[
            _token_result(),
            _result(_row(id=uuid.uuid4(), email=None, full_name="Nameless")),
        ]
    )
    with pytest.raises(ActivationError):
        await activate(db, raw_token="raw", new_password="GoodPass123!")


# ===========================================================================
# stage_activation_email — a link only when there is something to claim
# ===========================================================================
@pytest.mark.asyncio
async def test_email_carries_a_link_for_an_unclaimed_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app import apply_activation

    sent: dict = {}

    async def _capture(_db: object, **kw: object) -> None:
        sent.update(kw)

    monkeypatch.setattr(apply_activation, "enqueue_email", _capture)
    db = _db(
        executes=[_result(_row(email="guest+a@applicants.invalid", password_hash=None))],
        scalar="en",
    )
    included = await apply_activation.stage_activation_email(
        db,
        user_id=uuid.uuid4(),
        applicant_email="asha@example.com",
        applicant_name="Asha",
        job_title="Backend Engineer",
        company_id=uuid.uuid4(),
        company_name="Acme",
        now=datetime.now(tz=UTC),
    )
    assert included is True
    assert sent["template"] == "application_received"
    assert "/activate#" in sent["ctx"]["set_url"]


@pytest.mark.asyncio
async def test_email_omits_the_link_when_the_account_is_already_claimed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A second application from someone who already activated.

    They get the confirmation without an activation CTA — offering to create an
    account they already have would be confusing, and would mint a token that
    could only ever fail.
    """
    from app import apply_activation

    sent: dict = {}

    async def _capture(_db: object, **kw: object) -> None:
        sent.update(kw)

    monkeypatch.setattr(apply_activation, "enqueue_email", _capture)
    db = _db(
        executes=[_result(_row(email="asha@example.com", password_hash="$2b$12$x"))],
        scalar="hi",
    )
    included = await apply_activation.stage_activation_email(
        db,
        user_id=uuid.uuid4(),
        applicant_email="asha@example.com",
        applicant_name="Asha",
        job_title="Backend Engineer",
        company_id=uuid.uuid4(),
        company_name="Acme",
        now=datetime.now(tz=UTC),
    )
    assert included is False
    assert sent["ctx"]["set_url"] is None
    # The candidate's own language, not a hardcoded 'en'.
    assert sent["lang"] == "hi"


@pytest.mark.asyncio
async def test_activation_token_outlives_a_password_reset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The TTL is this module's, not the reset flow's one hour.

    An applicant reads their email that evening or at the weekend, and an
    expiry costs them their only route into their own application.
    """
    from app import apply_activation
    from app.config import settings

    monkeypatch.setattr(apply_activation, "enqueue_email", AsyncMock())
    now = datetime.now(tz=UTC)
    db = _db(
        executes=[_result(_row(email="guest+a@applicants.invalid", password_hash=None))],
        scalar="en",
    )
    await apply_activation.stage_activation_email(
        db,
        user_id=uuid.uuid4(),
        applicant_email="a@example.com",
        applicant_name="A",
        job_title="Role",
        company_id=uuid.uuid4(),
        company_name="Acme",
        now=now,
    )
    insert_params = db.execute.await_args_list[1].args[1]
    assert insert_params["exp"] - now == timedelta(hours=settings.apply_activation_ttl_hours)
    assert settings.apply_activation_ttl_hours > settings.password_reset_ttl_hours


# ===========================================================================
# H2 - an unverified password account is TAKEN OVER, never linked as-is
# L10 - the token is consumed by one conditional UPDATE
# ===========================================================================
async def _run_link(
    monkeypatch: pytest.MonkeyPatch, trust_row: object | None
) -> tuple[dict[str, str], AsyncMock]:
    from app import apply_activation

    monkeypatch.setattr(apply_activation, "_link_to_existing", AsyncMock())
    db = _db(
        executes=[
            _token_result(),
            _result(_row(id=uuid.uuid4(), email="victim@example.com", full_name="V")),
            _result(_row(id=uuid.uuid4())),  # consume won
            _result(),  # ...and its second UPDATE (other outstanding tokens)
            _result(trust_row),
        ],
        scalar=str(uuid.uuid4()),
    )
    out = await apply_activation.activate(db, raw_token="raw", new_password="MyNewPass123!")
    return out, db


def _sql(db: AsyncMock) -> list[str]:
    return [str(c.args[0]) for c in db.execute.call_args_list]


@pytest.mark.asyncio
async def test_unverified_password_account_is_taken_over_and_sessions_revoked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The pre-hijack: an attacker registered the victim's address first."""
    out, db = await _run_link(
        monkeypatch, _row(password_hash="$2b$attacker", email_verified_at=None, roles=["candidate"])
    )
    assert out["linked"] == "existing"
    assert out["revoke_sessions_for"], "sessions on the taken-over account must be revoked"
    takeover = [q for q in _sql(db) if "SET password_hash" in q and "email_verified_at" in q]
    assert takeover, "the attacker's password must be replaced and the address marked verified"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "trust_row",
    [
        _row(password_hash="$2b$x", email_verified_at=datetime.now(tz=UTC), roles=["candidate"]),
        _row(password_hash=None, email_verified_at=None, roles=["candidate"]),  # SSO/passwordless
        _row(password_hash="$2b$x", email_verified_at=None, roles=["hr_manager"]),  # staff
        None,
    ],
    ids=["verified", "passwordless", "staff-account", "row-vanished"],
)
async def test_verified_passwordless_and_staff_accounts_are_linked_not_rewritten(
    monkeypatch: pytest.MonkeyPatch, trust_row: object | None
) -> None:
    out, db = await _run_link(monkeypatch, trust_row)
    assert out["linked"] == "existing"
    assert not out["revoke_sessions_for"]
    assert not [q for q in _sql(db) if "SET password_hash" in q]


@pytest.mark.asyncio
async def test_token_consumption_is_one_conditional_update_and_losing_raises() -> None:
    from app.apply_activation import ActivationError, activate

    # consume returns no row: a concurrent submission already took the token
    db = _db(
        executes=[
            _token_result(),
            _result(_row(id=uuid.uuid4(), email="a@example.com", full_name="A")),
            _result(None),
        ],
        scalar=None,
    )
    with pytest.raises(ActivationError):
        await activate(db, raw_token="raw", new_password="GoodPass123!")
    consume_sql = _sql(db)[2]
    assert "consumed_at IS NULL" in consume_sql
    assert "expires_at >" in consume_sql
    assert "RETURNING" in consume_sql


@pytest.mark.asyncio
async def test_successful_consume_also_kills_the_users_other_outstanding_tokens() -> None:
    from app.apply_activation import _consume

    db = _db(executes=[_result(_row(id=uuid.uuid4())), _result()])
    await _consume(db, uuid.uuid4(), "raw", datetime.now(tz=UTC))
    sqls = _sql(db)
    assert len(sqls) == 2
    assert "user_id = :uid" in sqls[1] and "consumed_at IS NULL" in sqls[1]
