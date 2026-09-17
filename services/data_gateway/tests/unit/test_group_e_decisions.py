"""E2 — the final decision: one guarded writer, recorded against a person.

A hire or reject was reachable through three endpoints with three sets of rules,
and the one the decision queue used had no transition guard at all. The
database half is exercised in ``smoke_group_e_decisions``.
"""

from __future__ import annotations

import inspect
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

WF = uuid.uuid4()


# ===========================================================================
# The rules
# ===========================================================================
@pytest.mark.parametrize(
    ("decision", "status", "awaits", "workflow", "code"),
    [
        ("maybe", "held", True, WF, 400),
        ("hired", "hired", False, WF, 409),
        ("rejected", "rejected", False, WF, 409),
        # Still inside an automated round: the workflow has not finished.
        ("hired", "shortlisted", False, WF, 409),
        ("hired", "new", False, WF, 409),
        # Never hire over a rejection.
        ("hired", "rejected", False, None, 409),
        # Legacy / hand-run opening with no workflow: not before a shortlist.
        ("hired", "new", False, None, 409),
    ],
)
def test_what_is_refused(decision: str, status: str, awaits: bool, workflow: object,
                         code: int) -> None:
    from app.final_decision import refusal

    refused = refusal(decision, status, awaits, workflow)
    assert refused is not None and refused[0] == code


@pytest.mark.parametrize(
    ("decision", "status", "awaits", "workflow"),
    [
        # Reached a person: held, finished, or on a review round.
        ("hired", "held", True, WF),
        ("hired", "interviewed", True, WF),
        ("hired", "shortlisted", True, WF),
        # No workflow: as the pipeline board always allowed.
        ("hired", "shortlisted", False, None),
        ("hired", "interviewed", False, None),
        # Rejecting is always a person's explicit act, from anywhere live…
        ("rejected", "new", False, WF),
        ("rejected", "shortlisted", False, WF),
        # …and reverses a hire.
        ("rejected", "hired", False, WF),
    ],
)
def test_what_is_allowed(decision: str, status: str, awaits: bool, workflow: object) -> None:
    from app.final_decision import refusal

    assert refusal(decision, status, awaits, workflow) is None


# ===========================================================================
# The writer
# ===========================================================================
def _db(row: dict[str, Any] | None) -> AsyncMock:
    db = AsyncMock()
    result = MagicMock()
    result.mappings.return_value.first.return_value = row
    db.execute = AsyncMock(return_value=result)
    db.add = MagicMock()
    db.get = AsyncMock(return_value=MagicMock(email="c@example.com", full_name="C"))
    return db


def _row(**over: Any) -> dict[str, Any]:
    return {"status": "held", "applicant_id": uuid.uuid4(), "current_round_id": uuid.uuid4(),
            "workflow_id": WF, "requisition_id": uuid.uuid4(), "title": "Python Developer",
            "round_title": "Technical Test", "awaits_human": True, **over}


@pytest.fixture
def captured(monkeypatch: pytest.MonkeyPatch) -> dict[str, list]:
    import app.final_decision as fd
    import app.routers.hr_applicants as hra

    seen: dict[str, list] = {"moves": [], "rounds": [], "emails": []}

    async def _transition(_db: object, **kw: object) -> str:
        seen["moves"].append(kw)
        return "held"

    async def _round(_db: object, **kw: object) -> bool:
        seen["rounds"].append(kw)
        return True

    async def _email(_db: object, **kw: object) -> None:
        seen["emails"].append(kw)

    monkeypatch.setattr(fd, "record_transition", _transition)
    monkeypatch.setattr(fd, "record_round_move", _round)
    monkeypatch.setattr(hra, "email_applicant_decision", _email)

    from app.decision_reasons import ResolvedReason

    async def _resolve(_db: object, **kw: object) -> ResolvedReason:
        seen.setdefault("reasons", []).append(kw)
        return ResolvedReason(code=str(kw["code"]), label="Skills / competency fit",
                              requires_explanation=False)

    monkeypatch.setattr(fd, "resolve_reason", _resolve)
    return seen


@pytest.mark.asyncio
async def test_a_decision_is_recorded_against_the_person(captured: dict[str, list]) -> None:
    from app.final_decision import record_final_decision
    from app.models import AuditLog

    hr, company, enrolment = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    db = _db(_row())
    out = await record_final_decision(
        db, company_id=company, enrolment_id=enrolment, decision="hired",
        reason="  Strong technical round  ",
        reason_code="skills_fit", actor_user_id=hr, ip_address="1.2.3.4",
    )

    move = captured["moves"][0]
    assert move["to_status"] == "hired" and move["automated"] is False
    assert move["actor_user_id"] == hr and move["reason"] == "Strong technical round"
    # Off the round they were sitting on, so they are not still counted there.
    assert captured["rounds"][0]["to_round_id"] is None
    assert captured["rounds"][0]["automated"] is False
    audit = db.add.call_args.args[0]
    assert isinstance(audit, AuditLog)
    assert audit.actor_id == hr and audit.action == "enrolment.decision.hired"
    assert audit.details["reason"] == "Strong technical round"
    assert audit.details["previous_status"] == "held"
    # The hold is cleared along with it.
    assert any("held_at = NULL" in str(c.args[0]) for c in db.execute.call_args_list)
    assert captured["emails"][0]["decision"] == "hired"
    assert captured["emails"][0]["job_title"] == "Python Developer"
    assert out["status"] == "hired" and out["decided_by"] == str(hr)
    assert out["reversal"] is False


@pytest.mark.asyncio
async def test_reversing_a_hire_is_recorded_as_one(captured: dict[str, list]) -> None:
    from app.final_decision import record_final_decision

    db = _db(_row(status="hired", current_round_id=None, awaits_human=False))
    out = await record_final_decision(
        db, company_id=uuid.uuid4(), enrolment_id=uuid.uuid4(), decision="rejected",
        reason="Offer withdrawn after references",
        reason_code="skills_fit", actor_user_id=uuid.uuid4(),
    )
    assert out["reversal"] is True
    assert db.add.call_args.args[0].details["reversal"] is True
    assert captured["rounds"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", [None, "", "  ", "ok"])
async def test_no_reason_no_decision(captured: dict[str, list], reason: str | None) -> None:
    from app.final_decision import DecisionRefusedError, record_final_decision

    db = _db(_row())
    with pytest.raises(DecisionRefusedError) as exc:
        await record_final_decision(
            db, company_id=uuid.uuid4(), enrolment_id=uuid.uuid4(), decision="rejected",
            reason=reason,
        reason_code="skills_fit", actor_user_id=uuid.uuid4(),
        )
    assert exc.value.status_code == 422
    assert captured["moves"] == [] and not db.add.called


@pytest.mark.asyncio
async def test_a_refused_decision_writes_nothing(captured: dict[str, list]) -> None:
    from app.final_decision import DecisionRefusedError, record_final_decision

    db = _db(_row(status="shortlisted", awaits_human=False))
    with pytest.raises(DecisionRefusedError) as exc:
        await record_final_decision(
            db, company_id=uuid.uuid4(), enrolment_id=uuid.uuid4(), decision="hired",
            reason="Looks great so far",
        reason_code="skills_fit", actor_user_id=uuid.uuid4(),
        )
    assert exc.value.status_code == 409
    assert captured["moves"] == [] and captured["rounds"] == [] and not db.add.called


@pytest.mark.asyncio
async def test_another_companys_application_is_not_found(captured: dict[str, list]) -> None:
    from app.final_decision import DecisionRefusedError, record_final_decision

    db = _db(None)
    with pytest.raises(DecisionRefusedError) as exc:
        await record_final_decision(
            db, company_id=uuid.uuid4(), enrolment_id=uuid.uuid4(), decision="rejected",
            reason="Not a fit for the role",
        reason_code="skills_fit", actor_user_id=uuid.uuid4(),
        )
    assert exc.value.status_code == 404
    sql = str(db.execute.call_args.args[0])
    assert "e.company_id = :c" in sql and "FOR UPDATE OF e" in sql


# ===========================================================================
# The endpoints
# ===========================================================================
def test_the_decision_body_needs_a_real_decision_and_a_reason() -> None:
    from pydantic import ValidationError

    from app.routers.hr_requisitions import FinalDecisionIn

    ok = FinalDecisionIn(decision="hired", reason="Strong panel", reason_code="skills_fit")
    assert ok.decision == "hired" and ok.reason_code == "skills_fit"
    for bad in ({"decision": "maybe", "reason": "Strong panel", "reason_code": "skills_fit"},
                {"decision": "hired", "reason": "ok", "reason_code": "skills_fit"},
                {"decision": "hired", "reason_code": "skills_fit"},
                # PH4-O4: a final decision without a category is refused.
                {"decision": "hired", "reason": "Strong panel"}):
        with pytest.raises(ValidationError):
            FinalDecisionIn(**bad)


def test_the_generic_mover_hands_hire_and_reject_to_the_guarded_writer() -> None:
    from app.routers.hr_requisitions import record_decision, set_enrolment_status

    mover = inspect.getsource(set_enrolment_status)
    assert "if body.status in TERMINAL_STATUSES:" in mover
    assert mover.index("record_final_decision") < mover.index("record_transition(")
    assert "record_final_decision" in inspect.getsource(record_decision)


# ===========================================================================
# What the queue shows
# ===========================================================================
def test_the_queue_carries_what_a_reviewer_needs() -> None:
    from app.workflow_runner import decision_queue

    src = inspect.getsource(decision_queue)
    for field in ("applicant_id", "ats_recommendation", "ats_summary", "workflow_version",
                  "current_round_title", "waiting_days", "composite_percent",
                  "round_results"):
        assert f'"{field}"' in src, field
    assert "enrolment_state_since(e.id, e.created_at)" in src
    assert "rr.superseded_at IS NULL" in src


def test_round_results_can_be_read_for_one_application() -> None:
    from app.routers.hr_applicants import list_applicant_round_results

    src = inspect.getsource(list_applicant_round_results)
    assert "e.id = CAST(:e AS uuid)" in src


# ===========================================================================
# Closing an opening with undecided candidates can actually be confirmed
# ===========================================================================
def test_the_close_confirmation_the_console_sends_is_accepted() -> None:
    """The console sends ``acknowledge_unresolved`` as a JSON boolean. A
    ``dict[str, str]`` body refused it with a 422, so an opening with candidates
    still in it could not be closed from the screen that asks to confirm."""
    import typing

    from pydantic import TypeAdapter

    from app.routers.hr_requisitions import set_requisition_status

    body = typing.get_type_hints(set_requisition_status)["body"]
    sent = {"status": "closed", "acknowledge_unresolved": True}
    assert TypeAdapter(body).validate_python(sent) == sent


# ===========================================================================
# PH4-O4 — the structured reason travels with the decision
# ===========================================================================
@pytest.mark.asyncio
async def test_the_category_and_its_label_reach_the_ledger_and_the_audit(
    captured: dict[str, list],
) -> None:
    from app.final_decision import record_final_decision

    db = _db(_row())
    out = await record_final_decision(
        db, company_id=uuid.uuid4(), enrolment_id=uuid.uuid4(), decision="rejected",
        reason="Not enough distributed-systems depth", reason_code="skills_fit",
        actor_user_id=uuid.uuid4(),
    )
    move = captured["moves"][0]
    assert move["reason_code"] == "skills_fit"
    assert move["reason_label"] == "Skills / competency fit"
    audit = db.add.call_args.args[0]
    assert audit.details["reason_code"] == "skills_fit"
    assert audit.details["reason_label"] == "Skills / competency fit"
    # The free text is kept beside the category, not replaced by it.
    assert audit.details["reason"] == "Not enough distributed-systems depth"
    assert out["reason_code"] == "skills_fit"


@pytest.mark.asyncio
async def test_an_invalid_category_writes_nothing(
    captured: dict[str, list], monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.final_decision as fd
    from app.decision_reasons import ReasonError

    async def _refuse(_db: object, **_kw: object) -> None:
        raise ReasonError(422, "That reason category is not available.")

    monkeypatch.setattr(fd, "resolve_reason", _refuse)
    db = _db(_row())
    with pytest.raises(fd.DecisionRefusedError) as exc:
        await fd.record_final_decision(
            db, company_id=uuid.uuid4(), enrolment_id=uuid.uuid4(), decision="rejected",
            reason="Some reason text", reason_code="made_up", actor_user_id=uuid.uuid4(),
        )
    assert exc.value.status_code == 422
    assert captured["moves"] == [] and captured["rounds"] == [] and not db.add.called


@pytest.mark.asyncio
async def test_a_blocked_decision_says_why_before_asking_for_a_category(
    captured: dict[str, list],
) -> None:
    """Order matters: 'not ready to hire' is the useful refusal, not 'pick a reason'."""
    from app.final_decision import DecisionRefusedError, record_final_decision

    db = _db(_row(status="shortlisted", awaits_human=False))
    with pytest.raises(DecisionRefusedError) as exc:
        await record_final_decision(
            db, company_id=uuid.uuid4(), enrolment_id=uuid.uuid4(), decision="hired",
            reason="Looks great", reason_code=None, actor_user_id=uuid.uuid4(),
        )
    assert exc.value.status_code == 409
    assert "reasons" not in captured, "the category was checked before the decision itself"


def test_record_final_decision_has_no_default_for_the_category() -> None:
    """A default would let a caller forget it silently — the Phase 3 lesson,
    where a new required argument broke call sites nobody had grepped for."""
    from app.final_decision import record_final_decision

    param = inspect.signature(record_final_decision).parameters["reason_code"]
    assert param.default is inspect.Parameter.empty
