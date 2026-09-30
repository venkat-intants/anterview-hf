"""Reapplication cooldowns — PH3-B4b.

The window runs from the REJECTION, not from the application. Measuring from
the application would give exactly the wrong answer for the only case that
matters: somebody who applied six months ago and was turned down yesterday is
one day into their cooldown, not six months past it.
"""

from __future__ import annotations

import inspect
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "alembic" / "versions" / "20260916_0005_a7c9e1f3b5d8_ph3_b4b_reapplication.py"
)

NOW = datetime(2026, 9, 16, 12, 0, tzinfo=UTC)
REQ = uuid.UUID("11111111-1111-1111-1111-111111111111")
APPLICANT = uuid.UUID("22222222-2222-2222-2222-222222222222")
#: validate_answers returns (UUID, value) pairs — NOT strings. A test
#: that passes string keys cannot see that json.dumps refuses a UUID key.
QUESTION_ID = uuid.UUID("33333333-3333-3333-3333-333333333333")


def _db(row: dict | None) -> AsyncMock:
    db = AsyncMock()

    async def _execute(*_a: object, **_k: object) -> MagicMock:
        res = MagicMock()
        mapped = MagicMock()
        mapped.first = MagicMock(return_value=row)
        res.mappings = MagicMock(return_value=mapped)
        return res

    db.execute = AsyncMock(side_effect=_execute)
    return db


def _prior(**kw: object) -> dict:
    base = {
        "id": uuid.uuid4(),
        "reapply_override_at": None,
        "rejected_at": NOW - timedelta(days=10),
    }
    return {**base, **kw}


# ===========================================================================
# When the window applies at all
# ===========================================================================
@pytest.mark.asyncio
async def test_no_cooldown_configured_allows_everything() -> None:
    """The default. Nothing changes for any opening until somebody sets one."""
    from app.reapplication import check

    db = _db(_prior())
    verdict = await check(
        db, requisition_id=REQ, applicant_id=APPLICANT, cooldown_days=None, now=NOW
    )
    assert verdict.allowed is True
    # Not even queried: there is nothing to measure.
    assert db.execute.await_count == 0


@pytest.mark.asyncio
async def test_a_zero_cooldown_also_allows_everything() -> None:
    """Zero is a decision — "no waiting period" — and behaves like none."""
    from app.reapplication import check

    verdict = await check(
        _db(_prior()), requisition_id=REQ, applicant_id=APPLICANT,
        cooldown_days=0, now=NOW,
    )
    assert verdict.allowed is True


@pytest.mark.asyncio
async def test_somebody_who_never_applied_has_nothing_to_wait_out() -> None:
    from app.reapplication import check

    verdict = await check(
        _db(None), requisition_id=REQ, applicant_id=APPLICANT,
        cooldown_days=90, now=NOW,
    )
    assert verdict.allowed is True


@pytest.mark.asyncio
async def test_somebody_who_applied_but_was_never_rejected_is_not_in_a_window() -> None:
    """Their application may still be live, or they may have been hired. Either
    way there is no rejection to count from."""
    from app.reapplication import check

    verdict = await check(
        _db(_prior(rejected_at=None)), requisition_id=REQ, applicant_id=APPLICANT,
        cooldown_days=90, now=NOW,
    )
    assert verdict.allowed is True


# ===========================================================================
# The window itself
# ===========================================================================
@pytest.mark.asyncio
async def test_inside_the_window_is_refused_with_a_date() -> None:
    """A person refused with no date has no way to act on the refusal and will
    simply retry."""
    from app.reapplication import check

    verdict = await check(
        _db(_prior(rejected_at=NOW - timedelta(days=10))),
        requisition_id=REQ, applicant_id=APPLICANT, cooldown_days=90, now=NOW,
    )
    assert verdict.allowed is False
    assert verdict.until == NOW - timedelta(days=10) + timedelta(days=90)
    assert "2026-12-05" in verdict.message()


@pytest.mark.asyncio
async def test_past_the_window_is_allowed() -> None:
    from app.reapplication import check

    verdict = await check(
        _db(_prior(rejected_at=NOW - timedelta(days=91))),
        requisition_id=REQ, applicant_id=APPLICANT, cooldown_days=90, now=NOW,
    )
    assert verdict.allowed is True


@pytest.mark.asyncio
async def test_exactly_at_the_boundary_is_allowed() -> None:
    """The window is closed at its end: a 90-day cooldown means you may apply
    on day 90, not on day 91."""
    from app.reapplication import check

    verdict = await check(
        _db(_prior(rejected_at=NOW - timedelta(days=90))),
        requisition_id=REQ, applicant_id=APPLICANT, cooldown_days=90, now=NOW,
    )
    assert verdict.allowed is True


@pytest.mark.asyncio
async def test_the_window_is_measured_from_the_ledger_not_from_updated_at() -> None:
    """updated_at moves on a rescore, which would make the window whatever the
    reconciler last touched."""
    from app.reapplication import check

    db = _db(_prior())
    await check(
        db, requisition_id=REQ, applicant_id=APPLICANT, cooldown_days=90, now=NOW
    )
    sql = db.execute.await_args_list[0].args[0].text
    assert "stage_transitions" in sql
    assert "to_status = 'rejected'" in sql
    assert "updated_at" not in sql


@pytest.mark.asyncio
async def test_deleting_the_application_does_not_delete_the_cooldown() -> None:
    """Otherwise the window is trivially escapable."""
    from app.reapplication import check

    db = _db(_prior())
    await check(
        db, requisition_id=REQ, applicant_id=APPLICANT, cooldown_days=90, now=NOW
    )
    sql = db.execute.await_args_list[0].args[0].text
    assert "deleted_at IS NULL" not in sql


# ===========================================================================
# Override
# ===========================================================================
@pytest.mark.asyncio
async def test_an_override_lets_somebody_back_in() -> None:
    from app.reapplication import check

    verdict = await check(
        _db(_prior(reapply_override_at=NOW - timedelta(days=1))),
        requisition_id=REQ, applicant_id=APPLICANT, cooldown_days=90, now=NOW,
    )
    assert verdict.allowed is True
    assert verdict.reason == "override"


@pytest.mark.asyncio
async def test_the_override_is_scoped_to_the_company() -> None:
    """Another tenant's application must read as missing, not as forbidden."""
    from app.reapplication import grant_override

    db = _db(None)
    result = await grant_override(
        db, enrolment_id=uuid.uuid4(), company_id=uuid.uuid4(),
        actor_user_id=uuid.uuid4(), reason=None,
    )
    assert result is None
    assert "company_id = :c" in db.execute.await_args_list[0].args[0].text


def test_the_override_is_recorded_against_the_rejection_it_forgives() -> None:
    """On the enrolment, not on the person: a blanket exemption is a different
    and much larger grant."""
    from app.reapplication import grant_override

    src = inspect.getsource(grant_override)
    assert "UPDATE enrolments" in src
    assert "reapply_override_at" in src


def test_granting_an_override_is_audited() -> None:
    from app.routers.hr_requisitions import override_reapply_cooldown

    assert "enrolment.reapply_override" in inspect.getsource(override_reapply_cooldown)


def test_only_hr_can_override() -> None:
    from app.routers.hr_requisitions import override_reapply_cooldown

    assert "HrCtxDep" in str(
        inspect.signature(override_reapply_cooldown).parameters["ctx"].annotation
    )


# ===========================================================================
# The apply path
# ===========================================================================
# Two ORDERING properties, and they are structural assertions on purpose.
#
# Both are about what has already happened by the time the gate runs, which is
# a property of the function's shape rather than of any value it returns —
# there is nothing to observe from outside. A behavioural version would need
# the whole route driven against a real database with a real upload, which is
# `reapply-cooldown.spec.ts` in the browser suite; these are the cheap guard
# next to the code. Read them as such, and not as coverage of the rule itself
# — that is the executing tests further down.
#
# ("a live application is never told to wait" used to live here as a third
# string assertion. It is now `test_a_live_application_is_answered_not_refused`
# below, which calls the gate and reads the answer.)
def test_the_cv_is_stored_before_the_cooldown_is_checked() -> None:
    """DELIBERATELY REVERSED, and the reversal is the security property.

    This test used to require the opposite — gate first, so that a refusal
    cost no stored object. That is the tidier ordering and it was wrong, for a
    reason no unit test could see: it made the WORK the endpoint does depend
    on what is already known about the address. A live application and a
    cooldown returned before a 5 MB upload and ten writes; a first-time
    application and a reapplication performed all of it. The reply was
    identical by then, but two requests with a large PDF and a short client
    timeout still separated the states on latency, with the caller choosing
    the file size and so the size of the gap — and with storage unavailable
    the states that upload answered 503 while the states that returned early
    answered 201, which needs no timing at all.

    So the upload happens first on every submission and the branches that keep
    nothing delete it. The cost — an anonymous caller can make us write an
    object we immediately remove — is bounded by `_MAX_RESUME_BYTES` and by
    the burst and sustained rate limits on the route.

    The behavioural proof is in
    `tests/integration/test_ph3_cooldown_indistinguishable.py`
    (`test_every_state_does_the_same_work`, and the storage-outage test beside
    it), both of which fail if this ordering is put back.
    """
    from app.routers.public_apply import submit_application

    src = inspect.getsource(submit_application)
    assert src.index("_upload_to_s3") < src.index("reapplication_gate"), (
        "the gate runs before the upload again, so the work this endpoint does "
        "is once more an answer about the address"
    )
    # And what is not adopted is released. That now lives in `_refuse`, the
    # one function both doors refuse through, so it is asserted there — a
    # single place rather than the four copies this used to be spread over.
    from app.routers.public_apply import _refuse

    assert "_refuse(" in src, "the door no longer refuses through the shared path"
    assert "_release_unadopted" in inspect.getsource(_refuse), (
        "a refused submission no longer releases the CV it was made to upload, "
        "so the timing fix now costs a bucket full of other people's CVs"
    )


def test_the_cooldown_is_checked_after_the_cv_is_read() -> None:
    """Answering it earlier would let anyone holding the link discover, by
    typing addresses, who had been turned down for this role and when."""
    from app.routers.public_apply import submit_application

    src = inspect.getsource(submit_application)
    assert src.index("_extract_pdf_text") < src.index("reapplication_gate")


def test_both_doors_into_an_application_use_the_same_gate() -> None:
    """The draft route had its own copy of this decision and only the one-shot
    copy was fixed, so a rejected candidate who had saved their application for
    later was still told "we have your application" — the cooldown never ran on
    that route and an override let nobody through. Neither route may spell the
    rule out for itself again."""
    from app.routers.public_apply import submit_application, submit_draft

    for fn in (submit_application, submit_draft):
        src = inspect.getsource(fn)
        assert "reapplication_gate" in src, f"{fn.__name__} does not use the shared gate"
        assert "reapplication_stage" in src, f"{fn.__name__} never stages"
        assert "cooldown_check" not in src, f"{fn.__name__} still has its own copy"


def test_a_cooldown_refusal_is_indistinguishable_from_a_live_application() -> None:
    """This test used to require a 409 carrying the date, which was the oracle.

    The endpoint is anonymous and accepts any address, so a distinct answer for
    "rejected, come back on the 5th" told whoever typed it that a named person
    had applied, been turned down, and roughly when — employment-outcome data
    about a third party, handed to anyone with a public link. Both states now
    return the same body, and the date goes to the address by email.
    """
    from app.routers.public_apply import submit_application, submit_draft

    for fn in (submit_application, submit_draft):
        src = inspect.getsource(fn)
        assert "HTTP_409_CONFLICT" not in src, (
            f"{fn.__name__} still answers a cooldown differently from a live application"
        )
        assert "verdict.message()" not in src, (
            f"{fn.__name__} still puts the rejection date in the anonymous reply"
        )
        assert "_refuse(" in src, (
            f"{fn.__name__} does not refuse through the shared path, which is "
            "where five of six review rounds' defects came from"
        )
        # Every exit through the one reply, and held to the common deadline.
        # This replaces an `assert "_ALREADY_APPLIED" in src`, which asked for
        # the SECOND of two messages to still be there — a test that required
        # the difference it was named after. There is one message now, and it
        # leaves at one time.
        assert "_reply(name, floor_from=floor_from)" in src
        assert "ApplicationOut(" not in src, (
            f"{fn.__name__} builds its own reply, so it can differ again"
        )


def test_the_shared_refusal_path_always_mails_the_reason() -> None:
    """The reply says nothing; the ADDRESS is told. Asserted on `_refuse`,
    which both doors and both refusal branches go through, rather than on each
    door's own copy of it."""
    from app.routers.public_apply import _refuse

    body = inspect.getsource(_refuse)
    assert "_mail_cooldown_reason" in body, (
        "a cooldown refusal no longer tells the candidate why, and the reply "
        "deliberately cannot"
    )


def test_the_apply_query_reads_the_cooldown_setting() -> None:
    from app.routers.public_apply import _open_posting

    assert "reapply_cooldown_days" in inspect.getsource(_open_posting)


# ===========================================================================
# Configuration
# ===========================================================================
def test_the_setting_is_per_requisition() -> None:
    from app.routers.hr_requisitions import RequisitionOut, RequisitionPatch

    assert "reapply_cooldown_days" in RequisitionPatch.model_fields
    assert "reapply_cooldown_days" in RequisitionOut.model_fields


def test_a_cooldown_cannot_be_negative_or_effectively_permanent() -> None:
    from app.routers.hr_requisitions import RequisitionPatch

    with pytest.raises(ValueError, match="greater than or equal to 0"):
        RequisitionPatch(reapply_cooldown_days=-1)
    with pytest.raises(ValueError, match="less than or equal to 1095"):
        RequisitionPatch(reapply_cooldown_days=4000)
    assert RequisitionPatch(reapply_cooldown_days=0).reapply_cooldown_days == 0


def test_the_database_enforces_the_same_bounds() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "ck_job_requisitions_reapply_cooldown" in sql
    assert "1095" in sql


def test_the_default_is_no_cooldown() -> None:
    """A platform-wide default would silently start refusing applications
    nobody had decided to refuse."""
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "nullable=True" in sql
    assert "server_default" not in sql.split("def upgrade")[1].split("def downgrade")[0]


# ===========================================================================
# A rejected application must not answer as a live one
# ===========================================================================
# The already-applied branch runs before the cooldown check, deliberately: a
# person whose application is still open must never be told to wait. But it
# matched ANY enrolment, whatever its status, so a REJECTED candidate was told
# "we have your application" and the cooldown below could never run. That made
# both of this story's remaining criteria untrue of the product: the rule could
# refuse nobody through the form, and an override could let nobody through.
#
# The PH3-B4b smoke missed it because it soft-DELETES the enrolment before
# reapplying, which no real rejection does; with the row gone the branch missed
# and the cooldown ran. Found by the first browser test of this rule.


# ===========================================================================
# The gate itself — one predicate, both doors
# ===========================================================================
# These replace five tests that asserted on inspect.getsource() substrings.
# Those could not fail on any version of the code that compiled: one of them
# asserted that a variable appears before it is used. All three defects a code
# review later found in this path — an unadopted CV, the draft route never
# fixed at all, and an override spent when it was never consulted — survived
# them untouched. What follows executes the code instead.


@pytest.mark.asyncio
async def test_a_live_application_is_answered_not_refused() -> None:
    from app.reapplication import gate

    result = await gate(
        _db(None), requisition_id=REQ, cooldown_days=30,
        applicant_id=APPLICANT, enrolment_id=uuid.uuid4(),
        enrolment_status="shortlisted",
    )
    assert result.already_applied is True
    assert result.reapplying is False
    # And the cooldown is never consulted for someone whose application is open.
    assert result.verdict.allowed is True


@pytest.mark.asyncio
async def test_a_rejected_application_reaches_the_cooldown() -> None:
    """The defect this whole story turned on: `already_applied` matched ANY
    enrolment, so a rejected candidate never reached the rule."""
    from app.reapplication import gate

    db = _db(_prior(rejected_at=NOW - timedelta(days=2)))
    result = await gate(
        db, requisition_id=REQ, cooldown_days=30,
        applicant_id=APPLICANT, enrolment_id=uuid.uuid4(), enrolment_status="rejected",
    )
    assert result.already_applied is False, "a rejection is not a live application"
    assert result.reapplying is True
    assert result.verdict.allowed is False, "two days into a thirty-day window"


@pytest.mark.asyncio
async def test_a_first_time_applicant_passes_everything() -> None:
    from app.reapplication import gate

    result = await gate(
        _db(None), requisition_id=REQ, cooldown_days=30,
        applicant_id=None, enrolment_id=None, enrolment_status=None,
    )
    assert (result.already_applied, result.reapplying, result.verdict.allowed) == (
        False,
        False,
        True,
    )


# ===========================================================================
# Spending the override — only when it is what let them through
# ===========================================================================
@pytest.mark.asyncio
async def test_an_override_is_spent_when_it_is_what_allowed_the_reapplication() -> None:
    from app.reapplication import gate

    db = _db(_prior(reapply_override_at=NOW, rejected_at=NOW - timedelta(days=2)))
    result = await gate(
        db, requisition_id=REQ, cooldown_days=30,
        applicant_id=APPLICANT, enrolment_id=uuid.uuid4(), enrolment_status="rejected",
    )
    assert result.verdict.allowed is True
    assert result.verdict.reason == "override"
    assert result.spend_override is True


@pytest.mark.asyncio
async def test_an_override_is_not_spent_when_no_cooldown_is_configured() -> None:
    """`check` returns early without reading the grant when cooldown_days is
    falsy, so spending it here destroys an exception nobody used — on an
    opening that may be given a waiting period tomorrow."""
    from app.reapplication import gate

    db = _db(_prior(reapply_override_at=NOW))
    result = await gate(
        db, requisition_id=REQ, cooldown_days=None,
        applicant_id=APPLICANT, enrolment_id=uuid.uuid4(), enrolment_status="rejected",
    )
    assert result.reapplying is True
    assert result.verdict.allowed is True
    assert result.verdict.reason is None
    assert result.spend_override is False, "nothing consulted the grant"


@pytest.mark.asyncio
async def test_an_override_is_not_spent_when_the_window_had_simply_elapsed() -> None:
    from app.reapplication import gate

    db = _db(_prior(reapply_override_at=None, rejected_at=NOW - timedelta(days=99)))
    result = await gate(
        db, requisition_id=REQ, cooldown_days=30,
        applicant_id=APPLICANT, enrolment_id=uuid.uuid4(), enrolment_status="rejected",
    )
    assert result.verdict.allowed is True
    assert result.spend_override is False


@pytest.mark.asyncio
async def test_spending_the_override_makes_the_next_check_refuse() -> None:
    """End to end on the two functions that matter: granted, allowed, spent,
    refused. `consume_override` had no executing coverage at all."""
    from app.reapplication import check, consume_override

    granted = _prior(reapply_override_at=NOW, rejected_at=NOW - timedelta(days=2))
    db = _db(granted)
    before = await check(
        db, requisition_id=REQ, applicant_id=APPLICANT, cooldown_days=30
    )
    assert before.allowed is True
    assert before.reason == "override"

    await consume_override(db, enrolment_id=uuid.UUID(str(granted["id"])))
    sql = " ".join(str(c.args[0]) for c in db.execute.await_args_list)
    assert "reapply_override_at = NULL" in sql
    assert "reapply_override_by_user_id" not in sql, "who granted it stays on the row"

    spent = await check(
        _db(_prior(reapply_override_at=None, rejected_at=NOW - timedelta(days=2))),
        requisition_id=REQ,
        applicant_id=APPLICANT,
        cooldown_days=30,
    )
    assert spent.allowed is False, "the grant forgave one rejection, not all of them"






# ===========================================================================
# The confirmation half — which shipped, twice, with no executing coverage
# ===========================================================================
# A security re-audit found that `reapply_confirm` was never registered in
# `auth_tokens.secret_for`, so `hash_token` raised and the whole remediation
# was dead: no email was ever sent, every attempt staged for ever, and the
# confirm endpoint 500'd. 111 PH3 tests passed throughout, because not one of
# them touched the token. These do.


def test_the_reapply_token_kind_is_registered() -> None:
    """The defect that made the entire staged-reapplication flow inert."""
    from app.auth_tokens import hash_token, ttl_hours_for

    minted = hash_token("some-raw-token", "reapply_confirm")
    assert minted, "an unregistered kind raises rather than returning a hash"
    assert ttl_hours_for("reapply_confirm") > 0


def test_a_reapply_token_cannot_be_used_as_a_password_reset() -> None:
    """The HMAC is keyed BY KIND, so the same raw string hashes differently for
    each. That is what makes one unusable as the other if a link ever leaks —
    the query's `kind = :kind` filter is the second lock, not the only one."""
    from app.auth_tokens import hash_token

    raw = "the-same-raw-token"
    assert hash_token(raw, "reapply_confirm") != hash_token(raw, "password_reset")
    assert hash_token(raw, "reapply_confirm") != hash_token(raw, "email_verify")


def test_an_unknown_kind_is_still_refused() -> None:
    from app.auth_tokens import hash_token

    with pytest.raises(ValueError, match="unknown auth-token kind"):
        hash_token("x", "not_a_real_kind")


def _stage_db(*, pending: datetime | None, key: str | None = None) -> AsyncMock:
    """A db whose "is an attempt already pending?" lookup answers `pending`."""
    db = AsyncMock()

    async def _execute(*_a: object, **_k: object) -> MagicMock:
        res = MagicMock()
        mapped = MagicMock()
        mapped.first = MagicMock(
            return_value={"reapply_requested_at": pending, "reapply_resume_s3_key": key}
        )
        res.mappings = MagicMock(return_value=mapped)
        return res

    db.execute = AsyncMock(side_effect=_execute)
    return db


@pytest.mark.asyncio
async def test_staging_binds_the_token_to_this_attempt_alone() -> None:
    """Keyed on the person instead, one link applied everything they had
    staged — across companies. An attacker who knew a victim's address could
    then re-stage until the victim's own confirmation authenticated the
    attacker's CV and answers."""
    from app import reapplication
    from app.auth_tokens import hash_token, mint_token

    db = _stage_db(pending=None)
    raw = mint_token()
    await reapplication.stage(
        db,
        enrolment_id=uuid.uuid4(),
        company_id=uuid.uuid4(),
        resume_s3_key="applicants/c/a-new.pdf",
        answers=None,
        token_hash=hash_token(raw, "reapply_confirm"),
    )
    sql = " ".join(str(c.args[0]) for c in db.execute.await_args_list)
    assert "reapply_token_hash = :th" in sql
    params = [c.args[1] for c in db.execute.await_args_list if len(c.args) > 1]
    assert any(p.get("th") == hash_token(raw, "reapply_confirm") for p in params)
    # The RAW token is never stored — only its hash, as with every other
    # token in this system.
    assert not any(raw in str(p.values()) for p in params)


@pytest.mark.asyncio
async def test_restaging_supersedes_the_previous_attempts_cv() -> None:
    """Each staged attempt holds a CV that only these columns name. Overwriting
    without releasing the old one strands it where erasure cannot reach."""
    from app import reapplication
    from app.auth_tokens import hash_token, mint_token

    db = _stage_db(pending=NOW - timedelta(days=99),
                   key="applicants/c/a-previous.pdf")
    released = await reapplication.stage(
        db,
        enrolment_id=uuid.uuid4(),
        company_id=uuid.uuid4(),
        resume_s3_key="applicants/c/a-newer.pdf",
        answers=None,
        token_hash=hash_token(mint_token(), "reapply_confirm"),
    )
    assert released.staged is True
    assert released.superseded_key == "applicants/c/a-previous.pdf", (
        "an EXPIRED attempt's object is nobody's once replaced"
    )


@pytest.mark.asyncio
async def test_a_confirmation_finds_only_the_attempt_its_token_named() -> None:
    from app import reapplication

    db = AsyncMock()
    captured: list[dict] = []

    async def _execute(stmt: object, params: dict | None = None, **_k: object):
        captured.append(params or {})
        res = MagicMock()
        mapped = MagicMock()
        mapped.first = MagicMock(return_value=None)
        res.mappings = MagicMock(return_value=mapped)
        return res

    db.execute = AsyncMock(side_effect=_execute)
    await reapplication.staged_for_token(db, token_hash="abc123")
    sql = " ".join(str(c.args[0]) for c in db.execute.await_args_list)
    assert "reapply_token_hash = :th" in sql
    assert "user_id" not in sql, "scoping to the person is the flaw, not the fix"
    assert "FOR UPDATE" in sql
    assert captured and captured[0].get("th") == "abc123"


@pytest.mark.asyncio
async def test_confirming_re_evaluates_the_cooldown_rather_than_trusting_it() -> None:
    """Time passes between staging and confirming: the window may have moved
    and an override may have been spent elsewhere. The router's comment
    promised this while passing `spend_override=True` as a literal, which
    destroyed a recorded HR exception on every single confirmation."""
    from app import reapplication

    staged = {"status": "rejected", "reapply_resume_s3_key": None, "reapply_answers": None}

    async def _execute(*a: object, **k: object):
        res = MagicMock()
        mapped = MagicMock()
        mapped.first = MagicMock(return_value=staged)
        res.mappings = MagicMock(return_value=mapped)
        return res

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=_execute)
    db.scalar = AsyncMock(return_value=None)

    refusing = reapplication.CooldownVerdict(allowed=False, until=NOW)
    with (
        patch.object(reapplication, "check", AsyncMock(return_value=refusing)) as checked,
        patch("app.requisitions.record_transition", AsyncMock()) as moved,
        patch.object(reapplication, "consume_override", AsyncMock()) as spent,
    ):
        applied = await reapplication.confirm(
            db,
            enrolment_id=uuid.uuid4(),
            company_id=uuid.uuid4(),
            applicant_id=APPLICANT,
            requisition_id=REQ,
            cooldown_days=30,
        )

    checked.assert_awaited()
    assert applied.applied is False, "a window that closed since staging still refuses"
    moved.assert_not_awaited()
    spent.assert_not_awaited()


@pytest.mark.asyncio
async def test_confirming_spends_the_override_only_when_it_is_what_allowed_it() -> None:
    from app import reapplication

    staged = {"status": "rejected", "reapply_resume_s3_key": None, "reapply_answers": None}

    async def _execute(*a: object, **k: object):
        res = MagicMock()
        mapped = MagicMock()
        mapped.first = MagicMock(return_value=staged)
        res.mappings = MagicMock(return_value=mapped)
        return res

    for reason, should_spend in (("override", True), (None, False)):
        db = AsyncMock()
        db.execute = AsyncMock(side_effect=_execute)
        db.scalar = AsyncMock(return_value=None)
        verdict = reapplication.CooldownVerdict(allowed=True, reason=reason)
        with (
            patch.object(reapplication, "check", AsyncMock(return_value=verdict)),
            patch("app.requisitions.record_transition", AsyncMock(return_value="rejected")),
            patch.object(reapplication, "consume_override", AsyncMock()) as spent,
        ):
            await reapplication.confirm(
                db,
                enrolment_id=uuid.uuid4(),
                company_id=uuid.uuid4(),
                applicant_id=APPLICANT,
                requisition_id=REQ,
                cooldown_days=30,
            )
        assert spent.await_count == (1 if should_spend else 0), reason


@pytest.mark.asyncio
async def test_confirming_writes_answers_back_as_uuid_keys() -> None:
    """`validate_answers` produces (UUID, value) pairs and `json.dumps` refuses
    a UUID key, so staging stringifies them. If confirm does not restore them,
    `store_answers` binds a str to a uuid column. The earlier tests used
    `{"q1": ...}` — string keys production never produces — which is why the
    TypeError shipped."""
    from app import reapplication

    staged = {
        "status": "rejected",
        "reapply_resume_s3_key": None,
        "reapply_answers": {str(QUESTION_ID): "yes"},
    }

    async def _execute(*a: object, **k: object):
        res = MagicMock()
        mapped = MagicMock()
        mapped.first = MagicMock(return_value=staged)
        res.mappings = MagicMock(return_value=mapped)
        return res

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=_execute)
    db.scalar = AsyncMock(return_value=None)

    with (
        patch.object(
            reapplication, "check",
            AsyncMock(return_value=reapplication.CooldownVerdict(allowed=True)),
        ),
        patch("app.requisitions.record_transition", AsyncMock(return_value="rejected")),
        patch("app.application_questions.store_answers", AsyncMock()) as answers,
    ):
        await reapplication.confirm(
            db,
            enrolment_id=uuid.uuid4(),
            company_id=uuid.uuid4(),
            applicant_id=APPLICANT,
            requisition_id=REQ,
            cooldown_days=None,
        )

    answers.assert_awaited()
    written = answers.await_args.kwargs["answers"]
    assert written == [(QUESTION_ID, "yes")], "restored as UUIDs, not strings"


@pytest.mark.asyncio
async def test_a_stale_link_cannot_drag_a_moved_on_application_back() -> None:
    """The link lives for a week and the application does not stand still.

    HR may have used the override and reopened the person by hand, or moved
    them on. A stale link must not then pull a shortlisted or interviewing
    candidate back to `new` and replace the CV on their application with the
    staged one. On a `hired` enrolment `record_transition` refuses outright,
    which the endpoint's broad except turns into a permanent 503 rather than
    an answer.
    """
    from app import reapplication

    for moved_on in ("shortlisted", "interviewing", "hired"):
        staged = {
            "status": moved_on,
            "reapply_resume_s3_key": "applicants/c/a-new.pdf",
            "reapply_answers": None,
        }

        async def _execute(*a: object, _s: dict = staged, **k: object):
            res = MagicMock()
            mapped = MagicMock()
            mapped.first = MagicMock(return_value=_s)
            res.mappings = MagicMock(return_value=mapped)
            return res

        db = AsyncMock()
        db.execute = AsyncMock(side_effect=_execute)
        db.scalar = AsyncMock(return_value=None)
        with (
            patch("app.requisitions.record_transition", AsyncMock()) as moved,
            patch.object(reapplication, "consume_override", AsyncMock()) as spent,
        ):
            applied = await reapplication.confirm(
                db,
                enrolment_id=uuid.uuid4(),
                company_id=uuid.uuid4(),
                applicant_id=APPLICANT,
                requisition_id=REQ,
                cooldown_days=None,
            )
        assert applied.applied is False, moved_on
        moved.assert_not_awaited()
        # The override IS spent on this path now. HR moved this person on
        # themselves, so the exception granted for the reapplication that has
        # been overtaken has done its job; leaving it live would silently
        # forgive their NEXT rejection on this opening.
        spent.assert_awaited()
        # And the staged attempt is cleared, so the dead link stops resolving.
        sql = " ".join(str(c.args[0]) for c in db.execute.await_args_list)
        assert "reapply_token_hash = NULL" in sql, moved_on


@pytest.mark.asyncio
async def test_a_pending_attempt_cannot_be_replaced_by_a_later_submission() -> None:
    """The substitution attack, and the first-link-wins rule that closes it.

    Overwriting a pending attempt let an attacker who knew a rejected
    candidate's address stage their own CV and answers over the candidate's.
    The victim then received a second, indistinguishable "confirm your
    application" email and, by clicking it, authenticated the ATTACKER'S
    submission. Each overwrite also retired the candidate's live link and
    deleted the object behind it, which is a denial of reapplication on its
    own.
    """
    from app import reapplication

    # A real current timestamp, not the module's fixed NOW: the cutoff is
    # measured from the wall clock, so a frozen NOW reads as long expired and
    # the test would pass for the wrong reason.
    db = _stage_db(
        pending=datetime.now(tz=UTC), key="applicants/c/a-theirs.pdf"
    )
    result = await reapplication.stage(
        db,
        enrolment_id=uuid.uuid4(),
        company_id=uuid.uuid4(),
        resume_s3_key="applicants/c/a-attacker.pdf",
        answers={QUESTION_ID: "attacker's answer"},
        token_hash="attacker-hash",
    )
    assert result.staged is False, "the first link wins"
    assert result.superseded_key is None, "and nothing of theirs is deleted"
    sql = " ".join(str(c.args[0]) for c in db.execute.await_args_list)
    assert "UPDATE enrolments" not in sql, "nothing was written over"


@pytest.mark.asyncio
async def test_an_expired_attempt_may_be_replaced_and_reports_its_object() -> None:
    """Refusing for ever would lock a candidate out of their own second
    attempt. Past the token's life the old link cannot be redeemed, so there is
    nothing left to protect — and the object it held is now nobody's."""
    from app import reapplication

    db = _stage_db(pending=NOW - timedelta(days=99), key="applicants/c/a-stale.pdf")
    result = await reapplication.stage(
        db,
        enrolment_id=uuid.uuid4(),
        company_id=uuid.uuid4(),
        resume_s3_key="applicants/c/a-fresh.pdf",
        answers=None,
        token_hash="fresh-hash",
    )
    assert result.staged is True
    assert result.superseded_key == "applicants/c/a-stale.pdf"


# ===========================================================================
# Which mail an accepted submission earns — one path, three outcomes
# ===========================================================================
# `_stage_accepted_mail` was two hand-written copies, one per door, and the
# "send nothing" case was fixed on one of them only. It is now one function,
# and these are the tests it shipped without.
def _mail_args(**over: object) -> dict:
    base = {
        "user_id": uuid.uuid4(),
        "address": "someone@example.com",
        "applicant_name": "Stored Name",
        "job_title": "Backend Engineer",
        "company_id": uuid.uuid4(),
        "company_name": "Acme",
        "now": datetime.now(tz=UTC),
        "reapplying": False,
        "staged": False,
        "reapply_raw": None,
    }
    base.update(over)
    return base


@pytest.mark.asyncio
async def test_a_refused_reapplication_is_sent_nothing_at_all() -> None:
    """The case the previous copies got wrong on one door.

    "First link wins" means this submission recorded nothing, so there is no
    confirmation to send — and `stage_activation_email` has no dedupe key, so
    falling through to it let anyone who knows a rejected candidate's address
    drive "we have your application" at that inbox at the route's rate limit
    for the whole confirmation window, minting an auth token each time. The
    mail was also untrue: nothing was with the hiring team.
    """
    from app.routers import public_apply

    with (
        patch.object(public_apply, "stage_reapply_confirmation", AsyncMock()) as confirm,
        patch.object(public_apply, "stage_activation_email", AsyncMock()) as activate,
    ):
        await public_apply._stage_accepted_mail(
            AsyncMock(), **_mail_args(reapplying=True, staged=False)
        )

    confirm.assert_not_awaited()
    activate.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_staged_reapplication_gets_the_confirmation_link() -> None:
    """Not "your application is in" — that would be untrue while it waits, and
    it mints no token for somebody who has already claimed their account,
    leaving them nothing to confirm with."""
    from app.routers import public_apply

    with (
        patch.object(public_apply, "stage_reapply_confirmation", AsyncMock()) as confirm,
        patch.object(public_apply, "stage_activation_email", AsyncMock()) as activate,
    ):
        await public_apply._stage_accepted_mail(
            AsyncMock(),
            **_mail_args(reapplying=True, staged=True, reapply_raw="tok_abc"),
        )

    confirm.assert_awaited_once()
    assert confirm.await_args.kwargs["raw"] == "tok_abc"
    activate.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_first_application_gets_the_activation_mail() -> None:
    from app.routers import public_apply

    with (
        patch.object(public_apply, "stage_reapply_confirmation", AsyncMock()) as confirm,
        patch.object(public_apply, "stage_activation_email", AsyncMock()) as activate,
    ):
        await public_apply._stage_accepted_mail(AsyncMock(), **_mail_args())

    activate.assert_awaited_once()
    confirm.assert_not_awaited()


@pytest.mark.asyncio
async def test_the_mail_carries_the_name_the_caller_chose_to_pass() -> None:
    """Both doors pass `_email_name(existing, name)`, which prefers the STORED
    name. This pins that the helper does not reach for the request's own
    `full_name` behind the caller's back — an anonymous door must not be able
    to write a line of its own choosing into a third party's inbox."""
    from app.routers import public_apply

    with patch.object(public_apply, "stage_activation_email", AsyncMock()) as activate:
        await public_apply._stage_accepted_mail(
            AsyncMock(), **_mail_args(applicant_name="Stored Name")
        )

    assert activate.await_args.kwargs["applicant_name"] == "Stored Name"
