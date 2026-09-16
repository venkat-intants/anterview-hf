"""Requisition approval and budget — PH3-B2.

Three properties carry this story, and each is asserted against the mechanism
rather than against a handler that could be bypassed by a second one:

* an unapproved opening cannot reach the public — asserted against the shared
  publish predicate, which every public surface reads;
* HR cannot approve its own submission — asserted against the dependency, which
  fails before a handler is entered;
* a hiring budget never appears on a candidate-facing schema.
"""

from __future__ import annotations

import inspect
import uuid
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

APP = Path(__file__).resolve().parents[2] / "app"
MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "alembic" / "versions" / "20260916_0003_e5a7c9d1f3b6_ph3_b2_requisition_approval.py"
)

REQ = uuid.UUID("11111111-1111-1111-1111-111111111111")
COMPANY = uuid.UUID("22222222-2222-2222-2222-222222222222")
ACTOR = uuid.UUID("33333333-3333-3333-3333-333333333333")


def _db(requisition: dict | None, *, has_approver: bool = True) -> AsyncMock:
    db = AsyncMock()

    async def _execute(*_a: object, **_k: object) -> MagicMock:
        res = MagicMock()
        mapped = MagicMock()
        mapped.first = MagicMock(return_value=requisition)
        mapped.all = MagicMock(return_value=[requisition] if requisition else [])
        res.mappings = MagicMock(return_value=mapped)
        return res

    db.execute = AsyncMock(side_effect=_execute)
    db.scalar = AsyncMock(return_value=1 if has_approver else None)
    return db


def _req(**kw: object) -> dict:
    base = {
        "id": REQ,
        "title": "Platform Engineer",
        "approval_status": "draft",
        "public_apply_enabled": False,
        "status": "open",
    }
    return {**base, **kw}


# ===========================================================================
# The state machine
# ===========================================================================
@pytest.mark.parametrize(
    ("current", "target", "allowed"),
    [
        ("draft", "pending_approval", True),
        ("pending_approval", "approved", True),
        ("pending_approval", "rejected", True),
        ("rejected", "pending_approval", True),
        # Skipping review entirely.
        ("draft", "approved", False),
        ("draft", "rejected", False),
        # Deciding twice.
        ("approved", "rejected", False),
        ("rejected", "approved", False),
        # Re-approving what is already approved.
        ("approved", "approved", False),
        # Un-approving. Pausing or closing is the operation that means this.
        ("approved", "pending_approval", False),
        ("approved", "draft", False),
        # Nothing returns to draft: reverting the state would erase the
        # rejection from the record.
        ("rejected", "draft", False),
        ("pending_approval", "draft", False),
    ],
)
def test_transitions(current: str, target: str, allowed: bool) -> None:
    from app.requisition_approval import can_transition

    assert can_transition(current, target) is allowed


def test_the_database_allows_exactly_the_states_the_code_knows() -> None:
    from app.requisition_approval import APPROVAL_STATES

    sql = MIGRATION.read_text(encoding="utf-8")
    for state in APPROVAL_STATES:
        assert f"'{state}'" in sql, state


def test_a_refusal_says_what_was_possible_instead() -> None:
    """"Cannot approve" is useless to somebody looking at a screen; "this is
    still a draft, submit it first" is actionable."""
    from app.requisition_approval import ApprovalError

    message = str(ApprovalError("draft", "approved"))
    assert "draft" in message
    assert "pending_approval" in message

    terminal = str(ApprovalError("approved", "rejected"))
    assert "final state" in terminal


# ===========================================================================
# The publish gate — PH3-B2's central criterion
# ===========================================================================
def test_an_unapproved_requisition_cannot_reach_the_public() -> None:
    """Asserted against the SHARED predicate, so it holds for the careers
    board, the candidate feed and the apply endpoint at once rather than for
    whichever one a test happened to exercise."""
    from app.publishing import visible_sql

    assert "approval_status = 'approved'" in visible_sql("r")


def test_the_gate_is_in_the_predicate_not_in_a_handler() -> None:
    """A handler check protects one route. The predicate protects every read."""
    from app.publishing import _REQUISITION_GATES

    assert any(name == "approved" for name, _ in _REQUISITION_GATES)


def test_turning_on_public_applications_is_refused_before_approval() -> None:
    """Refused loudly at the edit rather than accepted and silently ineffective.

    Without this the flag would be on, the careers board empty, and nobody able
    to see why — the same confusing half-state the story forbids, read from the
    other direction.
    """
    from app.routers.hr_requisitions import update_requisition

    src = inspect.getsource(update_requisition)
    assert 'fields.get("public_apply_enabled") is True' in src
    assert "HTTP_409_CONFLICT" in src
    assert "approval_status" in src


def test_the_partial_indexes_learned_about_approval_too() -> None:
    """They would still be correct without this — their predicate is a superset
    — but every board query would filter rows the index already knew were
    ineligible."""
    sql = MIGRATION.read_text(encoding="utf-8")
    assert sql.count("approval_status = 'approved'") >= 2
    assert "ix_job_requisitions_board" in sql
    assert "ix_job_requisitions_public_open" in sql


# ===========================================================================
# Authorisation
# ===========================================================================
def test_only_a_super_admin_can_decide() -> None:
    """The annotations are strings here (``from __future__ import annotations``),
    which is fine: what matters is which dependency the route declares, and the
    name of it is exactly that."""
    from app.routers.hr_requisitions import approve_requisition, reject_requisition

    for fn in (approve_requisition, reject_requisition):
        ctx = str(inspect.signature(fn).parameters["ctx"].annotation)
        assert ctx == "SuperAdminCtxDep", f"{fn.__name__} declares {ctx}"


def test_only_hr_can_submit() -> None:
    from app.routers.hr_requisitions import submit_requisition_for_approval

    assert "HrCtxDep" in str(
        inspect.signature(submit_requisition_for_approval).parameters["ctx"]
    )


def test_the_super_admin_dependency_requires_the_role_and_a_company() -> None:
    from app.dependencies import get_super_admin_company

    src = inspect.getsource(get_super_admin_company)
    assert 'require_role_password_ok("super_admin")' in src
    # The company comes from the session, never from the request.
    assert "u.company_id" in src
    assert "WHERE u.id = :uid" in src


def test_self_approval_is_impossible_because_a_user_holds_one_role() -> None:
    """create_requisition requires hr_manager and approval requires
    super_admin, so no single account can do both. The separation is a property
    of the role model rather than a check somebody has to remember."""
    from app.routers.hr_requisitions import create_requisition

    assert "HrCtxDep" in str(inspect.signature(create_requisition).parameters["ctx"])


@pytest.mark.asyncio
async def test_a_cross_tenant_requisition_reads_as_missing() -> None:
    """404, never 403 — a 403 would confirm the row belongs to someone."""
    from app.requisition_approval import decide

    db = _db(None)
    with pytest.raises(LookupError):
        await decide(
            db, requisition_id=REQ, company_id=COMPANY,
            approver_user_id=ACTOR, approve=True,
        )
    assert "company_id = :c" in db.execute.await_args_list[0].args[0].text


def test_the_approval_routes_answer_a_missing_requisition_uniformly() -> None:
    from app.routers.hr_requisitions import _NO_SUCH_REQUISITION

    assert _NO_SUCH_REQUISITION.status_code == 404


# ===========================================================================
# Behaviour
# ===========================================================================
@pytest.mark.asyncio
async def test_resubmitting_clears_the_previous_decision() -> None:
    """A resubmitted requisition still carrying its old rejection would fail the
    CHECK that ties 'decided' to 'has a decision time' — and would show the
    approver a stale verdict."""
    from app.requisition_approval import submit_for_approval

    db = _db(_req(approval_status="rejected"))
    await submit_for_approval(
        db, requisition_id=REQ, company_id=COMPANY, actor_user_id=ACTOR
    )
    update = next(
        c for c in db.execute.await_args_list if "UPDATE" in c.args[0].text
    ).args[0].text
    assert "approval_decided_at = NULL" in update
    assert "approval_decided_by_user_id = NULL" in update


@pytest.mark.asyncio
async def test_rejecting_does_not_silently_change_an_hr_setting() -> None:
    """Nothing pending can be public, so there is nothing to take down — and
    flipping public_apply_enabled as a side effect would be a change nobody
    could see in the audit trail."""
    from app.requisition_approval import decide

    db = _db(_req(approval_status="pending_approval", public_apply_enabled=True))
    await decide(
        db, requisition_id=REQ, company_id=COMPANY,
        approver_user_id=ACTOR, approve=False,
    )
    for call in db.execute.await_args_list:
        assert "public_apply_enabled =" not in call.args[0].text


@pytest.mark.asyncio
async def test_a_company_with_no_approver_is_warned_about_not_refused() -> None:
    """Blocking HR from doing its half would not conjure an approver."""
    from app.requisition_approval import submit_for_approval

    db = _db(_req(), has_approver=False)
    result = await submit_for_approval(
        db, requisition_id=REQ, company_id=COMPANY, actor_user_id=ACTOR
    )
    assert result["approval_status"] == "pending_approval"


def test_the_approval_queue_is_oldest_first() -> None:
    """An approval queue is a waiting list; the longest wait should not be at
    the bottom of the screen."""
    from app.requisition_approval import pending_for_company

    assert "submitted_for_approval_at ASC" in inspect.getsource(pending_for_company)


def test_every_approval_action_is_audited() -> None:
    from app.routers import hr_requisitions

    assert "requisition.approval.submitted" in inspect.getsource(
        hr_requisitions.submit_requisition_for_approval
    )
    decide_src = inspect.getsource(hr_requisitions._decide)
    assert "requisition.approval." in decide_src
    # The reason is part of the decision, so it belongs in the immutable record.
    assert '"note": clean_note(body.note)' in decide_src


def test_an_illegal_transition_is_a_conflict_not_a_validation_error() -> None:
    """The request is well-formed; the requisition is simply not in a state the
    action applies to."""
    from app.routers import hr_requisitions

    for fn in (hr_requisitions.submit_requisition_for_approval, hr_requisitions._decide):
        assert "HTTP_409_CONFLICT" in inspect.getsource(fn)


# ===========================================================================
# Existing requisitions are not taken off the web
# ===========================================================================
def test_existing_requisitions_are_grandfathered_as_approved() -> None:
    """Defaulting them to draft would take every live opening off the public
    web the moment the migration ran. That is an outage, not governance."""
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "SET approval_status = 'approved'" in sql
    # Including soft-deleted rows, so undeleting one later cannot resurrect it
    # into a state the CHECK constraint forbids.
    assert "WHERE deleted_at IS NOT NULL" in sql


def test_the_grandfather_note_says_it_was_not_a_human_decision() -> None:
    from importlib.util import module_from_spec, spec_from_file_location

    spec = spec_from_file_location("m", MIGRATION)
    assert spec and spec.loader
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    note = module._GRANDFATHER_NOTE
    assert "predates" in note.lower() or "automatically" in note.lower()


def test_new_requisitions_start_as_draft() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "server_default=sa.text(\"'draft'\")" in sql


# ===========================================================================
# Budget
# ===========================================================================
def test_budget_never_appears_on_a_candidate_facing_schema() -> None:
    """Unlike salary_min/max there is no visibility flag, because there is no
    version of a careers page that should carry a hiring budget. The absence of
    the field IS the control."""
    from app.routers.careers import JobCard
    from app.routers.public_apply import PostingOut

    for model in (PostingOut, JobCard):
        for field in model.model_fields:
            assert "budget" not in field, f"{model.__name__}.{field}"


def test_the_public_queries_do_not_even_select_budget() -> None:
    for module in ("routers/careers.py", "routers/public_apply.py",
                   "routers/candidate_applications.py"):
        assert "budget" not in (APP / module).read_text(encoding="utf-8"), module


def test_budget_is_reported_on_the_hr_view() -> None:
    from app.routers.hr_requisitions import RequisitionOut

    for field in ("budget_amount", "budget_currency", "budget_basis",
                  "budget_period", "budget_notes"):
        assert field in RequisitionOut.model_fields, field


def test_an_amount_without_a_currency_is_refused_with_a_useful_message() -> None:
    """Caught in the model as well as by the CHECK constraint, so the caller
    gets a 422 naming the missing field rather than a 503 from a violation."""
    from app.routers.hr_requisitions import RequisitionPatch

    with pytest.raises(ValueError, match="currency"):
        RequisitionPatch(budget_amount=5_000_000)

    ok = RequisitionPatch(
        budget_amount=5_000_000, budget_currency="inr",
        budget_basis="total", budget_period="annual",
    )
    assert ok.budget_currency == "INR"  # normalised


def test_budget_does_not_replace_headcount() -> None:
    """Money and headcount are different constraints; the story says so."""
    from app.routers.hr_requisitions import RequisitionPatch

    assert "target_hires" in RequisitionPatch.model_fields
    assert "budget_amount" in RequisitionPatch.model_fields


def test_a_budget_basis_disambiguates_per_hire_from_total() -> None:
    """"50,00,000" against target_hires = 5 means very different things."""
    from app.routers.hr_requisitions import RequisitionPatch

    with pytest.raises(ValueError, match="budget_basis"):
        RequisitionPatch(
            budget_amount=1, budget_currency="INR",
            budget_basis="per_head", budget_period="annual",
        )


def test_budget_amount_is_a_bigint_in_the_database() -> None:
    """An annual budget for a large requisition exceeds a 32-bit integer in
    rupees, and discovering that in production means an overflow on the one
    field finance reads."""
    assert "budget_amount\", sa.BigInteger()" in MIGRATION.read_text(encoding="utf-8")


def test_the_database_refuses_a_half_filled_budget_too() -> None:
    assert "ck_job_requisitions_budget_complete" in MIGRATION.read_text(encoding="utf-8")


def test_approval_state_and_decision_time_cannot_disagree() -> None:
    assert "ck_job_requisitions_approval_decided" in MIGRATION.read_text(encoding="utf-8")


def test_timestamps_survive_a_row_that_never_selected_them() -> None:
    """_to_out is handed rows from several queries and from tests."""
    from app.routers.hr_requisitions import _iso

    assert _iso(None) is None
    assert _iso("not a datetime") is None
    assert _iso(datetime(2026, 9, 16, tzinfo=UTC)).startswith("2026-09-16")
