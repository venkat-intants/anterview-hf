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
def test_the_cooldown_is_checked_before_the_cv_is_stored() -> None:
    """A refusal should cost no stored object — the same ordering the consent
    and answer checks already use."""
    from app.routers.public_apply import submit_application

    src = inspect.getsource(submit_application)
    assert src.index("reapplication_gate") < src.index("_upload_to_s3")


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


def test_a_blocked_application_is_a_conflict_not_a_forbidden() -> None:
    from app.routers.public_apply import submit_application

    src = inspect.getsource(submit_application)
    assert "HTTP_409_CONFLICT" in src


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
# A reapplication is STAGED, and changes nothing until the address is proven
# ===========================================================================
# A security audit found that reopening on submission let one anonymous
# request — the apply link is not a secret, and the email is the only other
# input — move a real person's status, overwrite the screening answers they
# had already given, attach a stranger's PDF as the CV their application was
# submitted with, spend an override HR had granted them, and create a
# talent-pool consent they never gave. These pin that none of it happens
# before somebody follows a link sent to the address.


@pytest.mark.asyncio
async def test_staging_records_the_attempt_without_touching_the_application() -> None:
    from app import reapplication

    db = AsyncMock()
    await reapplication.stage(
        db,
        enrolment_id=uuid.uuid4(),
        company_id=uuid.uuid4(),
        resume_s3_key="applicants/c/a-deadbeef.pdf",
        answers={"q1": "yes"},
    )
    sql = " ".join(str(c.args[0]) for c in db.execute.await_args_list)
    assert "reapply_requested_at = now()" in sql
    assert "reapply_resume_s3_key = :k" in sql
    # The things an anonymous caller must NOT be able to move:
    assert "status" not in sql.replace("reapply_requested_at", ""), "no status change"
    assert "applied_resume_s3_key" not in sql, "the live CV is not repointed"
    assert "INSERT INTO application_answers" not in sql, "answers are not overwritten"


@pytest.mark.asyncio
async def test_the_staged_cv_is_named_by_a_column_so_erasure_finds_it() -> None:
    """Erasure collects a person's resume objects by reading the columns that
    name them; an object nothing names survives a completed erasure."""
    from app import reapplication

    db = AsyncMock()
    await reapplication.stage(
        db,
        enrolment_id=uuid.uuid4(),
        company_id=uuid.uuid4(),
        resume_s3_key="applicants/c/a-kept.pdf",
        answers=None,
    )
    params = [c.args[1] for c in db.execute.await_args_list if len(c.args) > 1]
    assert any(p.get("k") == "applicants/c/a-kept.pdf" for p in params)


@pytest.mark.asyncio
async def test_confirming_applies_everything_submission_did_not() -> None:
    from app import reapplication

    db = AsyncMock()
    staged = {
        "reapply_resume_s3_key": "applicants/c/a-new.pdf",
        "reapply_answers": {"q1": "yes"},
    }

    async def _execute(*a: object, **k: object) -> MagicMock:
        res = MagicMock()
        mapped = MagicMock()
        mapped.first = MagicMock(return_value=staged)
        res.mappings = MagicMock(return_value=mapped)
        return res

    db.execute = AsyncMock(side_effect=_execute)
    db.scalar = AsyncMock(return_value="applicants/c/a-new.pdf")

    with (
        patch("app.requisitions.record_transition", AsyncMock(return_value="rejected")) as moved,
        patch("app.application_questions.store_answers", AsyncMock()) as answers,
        patch.object(reapplication, "consume_override", AsyncMock()) as spent,
    ):
        applied = await reapplication.confirm(
            db,
            enrolment_id=uuid.uuid4(),
            company_id=uuid.uuid4(),
            spend_override=True,
        )

    assert applied is True
    moved.assert_awaited()
    assert moved.await_args.kwargs["to_status"] == "new"
    answers.assert_awaited()
    spent.assert_awaited()
    sql = " ".join(str(c.args[0]) for c in db.execute.await_args_list)
    assert "applied_resume_s3_key = :k" in sql, "the confirmed CV becomes the live one"


@pytest.mark.asyncio
async def test_confirming_twice_applies_nothing_the_second_time() -> None:
    from app import reapplication

    db = AsyncMock()

    async def _execute(*a: object, **k: object) -> MagicMock:
        res = MagicMock()
        mapped = MagicMock()
        mapped.first = MagicMock(return_value=None)  # nothing staged
        res.mappings = MagicMock(return_value=mapped)
        return res

    db.execute = AsyncMock(side_effect=_execute)
    with patch("app.requisitions.record_transition", AsyncMock()) as moved:
        applied = await reapplication.confirm(
            db, enrolment_id=uuid.uuid4(), company_id=uuid.uuid4(), spend_override=True
        )
    assert applied is False
    moved.assert_not_awaited(), "a link followed twice must not reopen twice"


@pytest.mark.asyncio
async def test_clearing_reports_the_object_it_released() -> None:
    """RETURNING on the UPDATE yields the NEW row, so a caller reading the
    column this statement just NULLed would get None and strand the object."""
    from app import reapplication

    db = AsyncMock()
    db.scalar = AsyncMock(return_value="applicants/c/a-abandoned.pdf")
    released = await reapplication.clear_staged(
        db, enrolment_id=uuid.uuid4(), company_id=uuid.uuid4()
    )
    assert released == "applicants/c/a-abandoned.pdf"


def test_neither_door_applies_a_reapplication_on_submission() -> None:
    """Structural, and worth it: the whole finding was that an anonymous
    request acted. Neither route may call confirm."""
    from app.routers.public_apply import submit_application, submit_draft

    for fn in (submit_application, submit_draft):
        src = inspect.getsource(fn)
        assert "reapplication_stage" in src, f"{fn.__name__} does not stage"
        assert "reapplication_confirm" not in src, (
            f"{fn.__name__} applies a reapplication without proof of the address"
        )
