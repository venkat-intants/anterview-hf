"""PH4 Wave 1 — regressions for the security audit and code review findings.

Each section names the finding it pins. The database halves — the ledger
trigger, the redaction and delete guards, a score that cannot be moved — are in
``tests/integration/test_ph4_scorecard_guarantees.py``; the end-to-end flows in
``tests/integration/smoke_ph4_scorecards.py``. This file is the fast layer, and
the structural checks that would otherwise only be caught by reading the code.
"""

from __future__ import annotations

import ast
import inspect
import pathlib
import re
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

APP = pathlib.Path(__file__).resolve().parents[2] / "app"
ALEMBIC = pathlib.Path(__file__).resolve().parents[2] / "alembic" / "versions"


def _mapping_result(rows: list[dict[str, Any]] | dict[str, Any] | None) -> MagicMock:
    res = MagicMock()
    if isinstance(rows, list):
        res.mappings.return_value.first.return_value = rows[0] if rows else None
        res.mappings.return_value.all.return_value = rows
    else:
        res.mappings.return_value.first.return_value = rows
        res.mappings.return_value.all.return_value = [rows] if rows else []
    return res


# ===========================================================================
# H1 — candidate-only flows fail closed for every staff role
# ===========================================================================
def _seeded_roles() -> set[str]:
    """Every role any migration seeds, read from the migrations themselves.

    So a role added by a future migration is covered by the test below without
    anybody remembering to add it here — which is exactly the failure H1 was.
    """
    names: set[str] = set()
    for path in ALEMBIC.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        for start in (m.end() for m in re.finditer(r"INSERT INTO roles", source)):
            # The statement runs to its blank line (or the next op. call).
            block = re.split(r"\n\s*\n|\bop\.", source[start:start + 1500], maxsplit=1)[0]
            names.update(re.findall(r"\(\s*'([a-z_]+)'\s*,", block))
    return names


def test_the_migrations_seed_the_roles_we_expect() -> None:
    """Guards the parser above: if it silently found nothing, the next test
    would pass vacuously."""
    roles = _seeded_roles()
    assert {"candidate", "admin", "hr_manager", "super_admin", "platform_owner",
            "interviewer"} <= roles, roles


def test_every_seeded_non_candidate_role_is_staff() -> None:
    from app.roles import CANDIDATE_ROLES, holds_non_candidate_role, is_candidate_only

    for role in _seeded_roles() - CANDIDATE_ROLES:
        assert holds_non_candidate_role([role]), role
        assert not is_candidate_only(["candidate", role]), role
    assert is_candidate_only(["candidate"]) and is_candidate_only([])


@pytest.mark.parametrize(
    "path", ["routers/sso_google.py", "routers/sso_naipunyam.py", "routers/onboarding.py"]
)
def test_candidate_only_flows_never_list_staff_roles(path: str) -> None:
    """A deny-list of staff roles is what let `interviewer` through. The flows
    must ask "anything that is not a candidate role?" instead."""
    source = (APP / path).read_text(encoding="utf-8")
    assert not re.search(r"IN \('hr_manager'", source), path
    assert "_PRIVILEGED_ROLES" not in source, path
    if path.startswith("routers/sso_"):
        assert source.count("NOT IN :candidate_roles") >= 1, path
        assert "CANDIDATE_ROLES" in source, path
    else:
        assert "is_candidate_only" in source, path


def test_google_sso_checks_both_the_gate_and_the_candidate_grant() -> None:
    source = (APP / "routers/sso_google.py").read_text(encoding="utf-8")
    assert source.count("NOT IN :candidate_roles") == 2


# ===========================================================================
# M1 — no path records hired/rejected without the decision writer
# ===========================================================================
def _call_names(fn: Any) -> list[str]:
    tree = ast.parse(inspect.getsource(fn).lstrip())
    return [
        n.func.id if isinstance(n.func, ast.Name) else getattr(n.func, "attr", "")
        for n in ast.walk(tree) if isinstance(n, ast.Call)
    ]


def test_the_applicant_board_decides_through_the_decision_writer() -> None:
    from app.routers.hr_applicants import update_applicant_status

    calls = _call_names(update_applicant_status)
    assert "record_final_decision" in calls
    src = inspect.getsource(update_applicant_status)
    # The terminal branch returns before the generic transition is written.
    assert src.index("if body.status in DECISIONS:") < src.index("await record_transition(")


class _Applicant:
    def __init__(self, status: str = "new") -> None:
        self.id = uuid.uuid4()
        self.status = status
        self.full_name = "Asha"
        self.email = "asha@example.com"
        self.updated_at = None


@pytest.fixture
def board(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    import app.routers.hr_applicants as ha
    from app.requisitions import ApplicationChoice

    state: dict[str, Any] = {"applicant": _Applicant(), "enrolment": uuid.uuid4(),
                             "decisions": [], "emails": []}

    async def _owned(_db: object, _c: object, _a: object) -> _Applicant:
        return state["applicant"]

    async def _choose(_db: object, **_kw: object) -> ApplicationChoice:
        e = state["enrolment"]
        return ApplicationChoice(e, "held", "Engineer", [e] if e else [])

    async def _email(_db: object, **kw: object) -> None:
        state["emails"].append(kw)

    monkeypatch.setattr(ha, "_get_owned", _owned)
    monkeypatch.setattr(ha, "choose_application", _choose)
    monkeypatch.setattr(ha, "_to_out", lambda a: {"status": a.status})
    monkeypatch.setattr(ha, "email_applicant_decision", _email)
    return state


async def _patch(status: str, **body: Any) -> Any:
    from app.routers.hr_applicants import StatusUpdate, update_applicant_status

    db = AsyncMock()
    db.add = MagicMock()
    out = await update_applicant_status(
        uuid.uuid4(), StatusUpdate(status=status, **body), MagicMock(),
        (uuid.uuid4(), uuid.uuid4()), db,
    )
    return out, db


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["hired", "rejected"])
async def test_a_reasonless_decision_on_the_board_is_refused(
    board: dict[str, Any], status: str
) -> None:
    """The exact call the old board made: `{status: 'rejected'}` and nothing else."""
    with pytest.raises(HTTPException) as exc:
        await _patch(status)
    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_a_board_decision_passes_reason_and_code_to_the_writer(
    board: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.routers.hr_applicants as ha

    seen: dict[str, Any] = {}

    async def _writer(_db: object, **kw: Any) -> dict[str, Any]:
        seen.update(kw)
        return {}

    monkeypatch.setattr(ha, "record_final_decision", _writer)
    _out, db = await _patch("rejected", reason="Not enough depth", reason_code="skills_fit")
    assert seen["decision"] == "rejected" and seen["reason_code"] == "skills_fit"
    assert seen["enrolment_id"] == board["enrolment"]
    db.commit.assert_awaited_once()
    assert board["emails"] == [], "the writer emails; the board must not email twice"


@pytest.mark.asyncio
async def test_a_refused_board_decision_rolls_back(
    board: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.routers.hr_applicants as ha
    from app.final_decision import DecisionRefusedError

    async def _writer(_db: object, **_kw: Any) -> dict[str, Any]:
        raise DecisionRefusedError(409, "still in the workflow")

    monkeypatch.setattr(ha, "record_final_decision", _writer)
    with pytest.raises(HTTPException) as exc:
        await _patch("hired", reason="Strong panel", reason_code="skills_fit")
    assert exc.value.status_code == 409
    assert exc.value.detail == "still in the workflow"


@pytest.mark.asyncio
async def test_a_person_with_no_application_still_needs_a_reason_and_the_rules(
    board: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.routers.hr_applicants as ha
    from app.decision_reasons import ResolvedReason

    board["enrolment"] = None
    with pytest.raises(HTTPException) as exc:
        await _patch("rejected")
    assert exc.value.status_code == 422
    # Same rules as an application on no workflow: no hire from 'new'.
    with pytest.raises(HTTPException) as exc:
        await _patch("hired", reason="Great", reason_code="skills_fit")
    assert exc.value.status_code == 409

    async def _resolve(_db: object, **_kw: Any) -> ResolvedReason:
        return ResolvedReason(code="experience", label="Experience", requires_explanation=False)

    monkeypatch.setattr(ha, "resolve_reason", _resolve)
    _out, db = await _patch("rejected", reason="Too junior", reason_code="experience")
    audit = db.add.call_args.args[0]
    assert audit.details["reason_code"] == "experience"
    assert audit.details["reason_label"] == "Experience"
    assert board["applicant"].status == "rejected"
    assert board["emails"], "the candidate is still told"


@pytest.mark.parametrize("to_status", ["hired", "rejected", "new", "held"])
def test_a_hold_release_cannot_name_a_decision(to_status: str) -> None:
    from app.routers.hr_workflows import ReleaseIn

    with pytest.raises(ValidationError):
        ReleaseIn(to_status=to_status)


@pytest.mark.asyncio
@pytest.mark.parametrize("to_status", ["hired", "rejected"])
async def test_release_hold_itself_refuses_a_decision(to_status: str) -> None:
    """Behind the request model as well: release_hold has other callers' worth
    of reach, and the ledger trigger is the last line, not the first."""
    from app.workflow_runner import release_hold

    db = AsyncMock()
    out = await release_hold(
        db, enrolment_id=uuid.uuid4(), actor_user_id=uuid.uuid4(), to_status=to_status
    )
    assert out.action == "noop"
    db.execute.assert_not_awaited()


def test_the_ledger_refuses_a_codeless_decision_at_the_database() -> None:
    source = next(ALEMBIC.glob("*f4b6d8e0a2c3*")).read_text(encoding="utf-8")
    assert "BEFORE INSERT ON stage_transitions" in source
    assert "NEW.reason_code IS NULL" in source
    assert "DROP TRIGGER IF EXISTS stage_transitions_require_reason_code" in source


# ===========================================================================
# M2 — independence holds for the HR managers who interview
# ===========================================================================
def _withdraw_row(**over: Any) -> dict[str, Any]:
    base = {
        "id": uuid.uuid4(), "status": "assigned", "superseded_at": None,
        "interviewer_user_id": uuid.uuid4(), "enrolment_id": uuid.uuid4(),
        "corrects_id": None, "assigned_by_user_id": uuid.uuid4(),
        "requisition_id": uuid.uuid4(), "candidate_name": "Asha", "job_title": "Eng",
        "requisition_owner": uuid.uuid4(), "round_title": "Tech", "candidate_erased": False,
    }
    return {**base, **over}


@pytest.fixture
def quiet_notify(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[Any]]:
    import app.interviewer_scorecards as sc

    sent: dict[str, list[Any]] = {"interviewer": [], "hr": []}

    async def _note(_db: object, **kw: Any) -> None:
        sent["interviewer"].append(kw)

    async def _hr(_db: object, **kw: Any) -> None:
        sent["hr"].append(kw)

    monkeypatch.setattr(sc, "create_notification", _note)
    monkeypatch.setattr(sc, "_notify_hr", _hr)
    return sent


async def _withdraw(
    row: dict[str, Any], actor: uuid.UUID, *, other_hr: int = 1, **kw: Any
) -> AsyncMock:
    from app.interviewer_scorecards import RequestMeta, withdraw

    db = AsyncMock()
    db.add = MagicMock()
    db.scalar = AsyncMock(return_value=other_hr)  # other active HR managers
    db.execute = AsyncMock(return_value=_mapping_result(row))
    await withdraw(db, company_id=uuid.uuid4(), scorecard_id=row["id"], actor=actor,
                   meta=RequestMeta(), **kw)
    return db


@pytest.mark.asyncio
async def test_an_interviewer_cannot_withdraw_themselves(quiet_notify: Any) -> None:
    from app.interviewer_scorecards import ScorecardError

    row = _withdraw_row()
    with pytest.raises(ScorecardError) as exc:
        await _withdraw(row, row["interviewer_user_id"], reason=None, other_hr=1)
    assert exc.value.status_code == 409 and "another HR manager" in exc.value.detail


@pytest.mark.asyncio
async def test_the_only_hr_manager_can_step_off_a_panel(quiet_notify: Any) -> None:
    """Nobody else could do it for them. Safe, because assign() refuses their
    re-assignment once anyone else's scorecard is readable."""
    row = _withdraw_row()
    db = await _withdraw(row, row["interviewer_user_id"], reason=None, other_hr=0)
    assert "status = 'withdrawn'" in str(db.execute.await_args_list[-1].args[0])


@pytest.mark.asyncio
async def test_an_open_correction_is_not_withdrawn_except_on_removal(quiet_notify: Any) -> None:
    from app.interviewer_scorecards import ScorecardError

    row = _withdraw_row(status="in_progress", corrects_id=uuid.uuid4())
    with pytest.raises(ScorecardError) as exc:
        await _withdraw(row, uuid.uuid4(), reason=None)
    assert exc.value.status_code == 409
    db = await _withdraw(row, uuid.uuid4(), reason="Left", removing_interviewer=True)
    assert "status = 'withdrawn'" in str(db.execute.await_args_list[-1].args[0])


@pytest.mark.asyncio
async def test_a_withdrawal_reason_never_reaches_the_audit_log_or_a_notification(
    quiet_notify: dict[str, list[Any]],
) -> None:
    """M4(b): audit rows and notifications outlive an erasure; the text does not
    belong in either. The scorecard keeps it, where step 5f redacts it."""
    row = _withdraw_row()
    actor = uuid.uuid4()
    reason = "Conflict: Asha worked with the interviewer at Acme"
    db = await _withdraw(row, actor, reason=reason)
    details = db.add.call_args.args[0].details
    assert "reason" not in details
    assert details["has_reason"] is True and details["reason_chars"] == len(reason)
    flat = repr(quiet_notify)
    assert "Acme" not in flat and "Conflict" not in flat
    assert quiet_notify["hr"][0]["exclude"] == {actor}, "the person withdrawing is not told"


@pytest.mark.asyncio
async def test_no_reason_is_recorded_against_an_erased_candidate(quiet_notify: Any) -> None:
    row = _withdraw_row(candidate_erased=True)
    db = await _withdraw(row, uuid.uuid4(), reason="Something about the candidate")
    update = db.execute.await_args_list[-1]
    assert update.args[1]["why"] is None


def _enrolment_row(**over: Any) -> dict[str, Any]:
    base = {"id": uuid.uuid4(), "workflow_id": uuid.uuid4(), "status": "held",
            "requisition_id": uuid.uuid4(), "full_name": "Asha", "job_title": "Eng",
            "applicant_user_id": None, "applicant_email": "asha@example.com",
            "candidate_erased": False}
    return {**base, **over}


@pytest.fixture
def panel(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    import app.interviewer_scorecards as sc

    hr = uuid.uuid4()
    iv = uuid.uuid4()
    state: dict[str, Any] = {"hr": hr, "iv": iv, "people": [
        {"user_id": str(hr), "full_name": "Hari HR", "email": "hari@co.test",
         "role": "hr_manager", "reads_scorecards": True},
        {"user_id": str(iv), "full_name": "Ivy", "email": "ivy@co.test",
         "role": "interviewer", "reads_scorecards": False},
    ]}

    async def _people(_db: object, **_kw: object) -> list[dict[str, Any]]:
        return state["people"]

    async def _criteria(_db: object, ids: list[uuid.UUID]) -> dict[str, Any]:
        return {str(ids[0]): [{"competency_id": "ps", "competency_name": "PS", "weight": 1}]}

    monkeypatch.setattr(sc, "list_assignable_interviewers", _people)
    monkeypatch.setattr(sc, "load_criteria", _criteria)
    return state


async def _assign(panel: dict[str, Any], who: list[uuid.UUID], *results: MagicMock) -> AsyncMock:
    from app.interviewer_scorecards import RequestMeta, assign

    db = AsyncMock()
    db.add = MagicMock()
    db.execute = AsyncMock(side_effect=list(results))
    await assign(db, company_id=uuid.uuid4(), enrolment_id=uuid.uuid4(), round_id=uuid.uuid4(),
                 interviewer_user_ids=who, assigned_by=panel["hr"], due_at=None,
                 meta=RequestMeta())
    return db


def _round_row(workflow_id: uuid.UUID) -> dict[str, Any]:
    return {"id": uuid.uuid4(), "workflow_id": workflow_id, "kind": "human_review",
            "title": "Tech", "deadline_days": 7}


@pytest.mark.asyncio
async def test_an_hr_reader_cannot_join_a_panel_after_others_submitted(
    panel: dict[str, Any],
) -> None:
    from app.interviewer_scorecards import ScorecardError

    enrolment = _enrolment_row()
    existing = [{"interviewer_user_id": panel["iv"], "status": "submitted",
                 "superseded_at": None}]
    with pytest.raises(ScorecardError) as exc:
        await _assign(panel, [panel["hr"]], _mapping_result(enrolment),
                      _mapping_result(_round_row(enrolment["workflow_id"])),
                      _mapping_result(existing))
    assert exc.value.status_code == 409 and "independent" in exc.value.detail


@pytest.mark.asyncio
async def test_an_hr_reader_already_on_the_panel_can_be_resent(panel: dict[str, Any]) -> None:
    """Re-sending the same panel stays harmless: their card predates what they saw."""
    from app.interviewer_scorecards import ScorecardError

    enrolment = _enrolment_row()
    existing = [
        {"interviewer_user_id": panel["iv"], "status": "submitted", "superseded_at": None},
        {"interviewer_user_id": panel["hr"], "status": "in_progress", "superseded_at": None},
    ]
    try:
        await _assign(panel, [panel["hr"]], _mapping_result(enrolment),
                      _mapping_result(_round_row(enrolment["workflow_id"])),
                      _mapping_result(existing), MagicMock())
    except ScorecardError as exc:  # pragma: no cover — the assertion below explains
        pytest.fail(f"a live panel member was refused: {exc.detail}")
    except (StopAsyncIteration, StopIteration, TypeError, AttributeError):
        pass  # got past the guard into the insert, which this mock does not model


@pytest.mark.asyncio
async def test_an_interviewer_who_cannot_read_scorecards_can_join_late(
    panel: dict[str, Any],
) -> None:
    from app.interviewer_scorecards import ScorecardError

    enrolment = _enrolment_row()
    try:
        await _assign(panel, [panel["iv"]], _mapping_result(enrolment),
                      _mapping_result(_round_row(enrolment["workflow_id"])))
    except ScorecardError as exc:  # pragma: no cover
        pytest.fail(f"refused: {exc.detail}")
    except (StopAsyncIteration, StopIteration, TypeError, AttributeError):
        pass


@pytest.mark.asyncio
@pytest.mark.parametrize("match", ["account", "email"])
async def test_nobody_interviews_themselves(panel: dict[str, Any], match: str) -> None:
    """L7 — an internal candidate who is also staff."""
    from app.interviewer_scorecards import ScorecardError

    enrolment = _enrolment_row(
        applicant_user_id=panel["iv"] if match == "account" else None,
        applicant_email="IVY@co.test" if match == "email" else "someone@else.test",
    )
    with pytest.raises(ScorecardError) as exc:
        await _assign(panel, [panel["iv"]], _mapping_result(enrolment),
                      _mapping_result(_round_row(enrolment["workflow_id"])))
    assert exc.value.status_code == 409 and "themselves" in exc.value.detail


@pytest.mark.asyncio
async def test_hr_view_hides_correction_reasons_while_the_viewer_owes_a_scorecard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.interviewer_scorecards as sc

    viewer, peer = uuid.uuid4(), uuid.uuid4()
    rnd = uuid.uuid4()

    async def _criteria(_db: object, _ids: list[uuid.UUID]) -> dict[str, Any]:
        return {str(rnd): []}

    monkeypatch.setattr(sc, "load_criteria", _criteria)

    def card(who: uuid.UUID, status: str, **over: Any) -> dict[str, Any]:
        base = {"id": uuid.uuid4(), "round_id": rnd, "interviewer_user_id": who,
                "status": status, "due_at": None, "summary": "text", "submitted_at": None,
                "withdrawn_at": None, "withdrawn_reason": None, "corrects_id": None,
                "correction_reason": None, "superseded_at": None, "redacted_at": None,
                "created_at": None, "corrected_after_peers_visible": False,
                "interviewer_name": "x", "interviewer_email": "x", "round_title": "Tech",
                "position": 0}
        return {**base, **over}

    rows = [
        card(viewer, "in_progress"),
        card(peer, "in_progress", corrects_id=uuid.uuid4(),
             correction_reason="Design should have been 4, not 2",
             corrected_after_peers_visible=True),
    ]
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=1)
    db.execute = AsyncMock(return_value=_mapping_result(rows))
    out = await sc.scorecards_for_enrolment(
        db, company_id=uuid.uuid4(), enrolment_id=uuid.uuid4(), viewer_user_id=viewer
    )
    peer_entry = out["rounds"][0]["scorecards"][1]
    assert out["rounds"][0]["hidden_until_you_submit"] is True
    assert peer_entry["correction_reason"] is None
    assert peer_entry["corrected_after_peers_visible"] is True

    out = await sc.scorecards_for_enrolment(
        db, company_id=uuid.uuid4(), enrolment_id=uuid.uuid4(), viewer_user_id=uuid.uuid4()
    )
    assert out["rounds"][0]["scorecards"][1]["correction_reason"].startswith("Design")


@pytest.mark.asyncio
async def test_a_correction_records_whether_peers_were_visible_not_its_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.interviewer_scorecards as sc
    from app.interviewer_scorecards import RequestMeta

    me = uuid.uuid4()
    card = {"id": uuid.uuid4(), "status": "submitted", "superseded_at": None,
            "enrolment_status": "held", "candidate_erased": False,
            "enrolment_id": uuid.uuid4(), "round_id": uuid.uuid4(), "candidate_name": "Asha"}

    async def _owned(_db: object, **_kw: object) -> dict[str, Any]:
        return card

    hr_calls: list[dict[str, Any]] = []

    async def _hr(_db: object, **kw: Any) -> None:
        hr_calls.append(kw)

    monkeypatch.setattr(sc, "_load_owned", _owned)
    monkeypatch.setattr(sc, "_notify_hr", _hr)
    db = AsyncMock()
    db.add = MagicMock()
    db.scalar = AsyncMock(return_value=True)
    reason = "I scored Design 2 but meant 4 for Asha"
    out = await sc.open_correction(db, scorecard_id=card["id"], interviewer_user_id=me,
                                   company_id=uuid.uuid4(), reason=reason, meta=RequestMeta())
    assert out["corrected_after_peers_visible"] is True
    insert = next(c for c in db.execute.await_args_list
                  if "INSERT INTO interviewer_scorecards" in str(c.args[0]))
    assert insert.args[1]["peers"] is True
    details = db.add.call_args.args[0].details
    assert "reason" not in details and details["reason_chars"] == len(reason)
    assert details["corrected_after_peers_visible"] is True
    assert "body" not in hr_calls[0], "the reason is not copied into a notification"


# ===========================================================================
# M3 — withdrawal ends access; notes close and are purged
# ===========================================================================
def test_a_withdrawn_assignment_is_not_the_interviewers_any_more() -> None:
    from app.interviewer_scorecards import _OWNED_SQL

    assert "s.status <> 'withdrawn'" in _OWNED_SQL


@pytest.mark.parametrize("fn", ["get_notes", "save_notes", "kit_for_scorecard"])
def test_kit_and_notes_load_through_the_ownership_query(fn: str) -> None:
    import app.interview_kits as ik

    assert "_load_owned" in _call_names(getattr(ik, fn))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("over", "fragment"),
    [({"enrolment_status": "rejected"}, "final decision"),
     ({"candidate_erased": True}, "erased")],
)
async def test_notes_close_after_a_decision_or_an_erasure(
    monkeypatch: pytest.MonkeyPatch, over: dict[str, Any], fragment: str
) -> None:
    import app.interview_kits as ik
    from app.interviewer_scorecards import ScorecardError

    card = {"enrolment_status": "held", "candidate_erased": False,
            "enrolment_id": uuid.uuid4(), "round_id": uuid.uuid4(), **over}
    seen: dict[str, Any] = {}

    async def _owned(_db: object, **kw: Any) -> dict[str, Any]:
        seen.update(kw)
        return card

    monkeypatch.setattr(ik, "_load_owned", _owned)
    db = AsyncMock()
    with pytest.raises(ScorecardError) as exc:
        await ik.save_notes(db, scorecard_id=uuid.uuid4(), interviewer_user_id=uuid.uuid4(),
                            company_id=uuid.uuid4(), notes="more")
    assert exc.value.status_code == 409 and fragment in exc.value.detail
    assert seen["for_update"] is True
    db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_the_notes_purge_dry_run_deletes_nothing() -> None:
    from app.interview_kits import purge_expired_notes

    ids = MagicMock()
    ids.scalars.return_value.all.return_value = [uuid.uuid4(), uuid.uuid4()]
    db = AsyncMock()
    db.execute = AsyncMock(return_value=ids)
    assert await purge_expired_notes(db, dry_run=True) == 2
    assert db.execute.await_count == 1


@pytest.mark.asyncio
async def test_the_notes_purge_deletes_what_it_selected() -> None:
    from app.interview_kits import purge_expired_notes

    selected = [uuid.uuid4()]
    first = MagicMock()
    first.scalars.return_value.all.return_value = selected
    second = MagicMock()
    second.rowcount = 1
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[first, second])
    assert await purge_expired_notes(db) == 1
    select_sql = str(db.execute.await_args_list[0].args[0])
    assert "e.status IN ('hired', 'rejected')" in select_sql
    assert "stage_transitions" in select_sql, "dated by the ledger, not updated_at"
    assert "s.status <> 'withdrawn'" in select_sql
    delete = db.execute.await_args_list[1]
    assert "DELETE FROM interviewer_notes" in str(delete.args[0])
    assert delete.args[1]["ids"] == selected


def test_the_nightly_retention_job_purges_notes_and_honours_dry_run() -> None:
    from app.main import _run_retention_job

    src = inspect.getsource(_run_retention_job)
    assert "purge_expired_notes" in src and "retention_dry_run" in src


# ===========================================================================
# M4 — nothing new is written about an erased candidate
# ===========================================================================
@pytest.mark.parametrize("fn", ["assign", "withdraw"])
def test_write_paths_that_load_an_applicant_know_about_erasure(fn: str) -> None:
    import app.interviewer_scorecards as sc

    src = inspect.getsource(getattr(sc, fn))
    assert "erasure_requests" in src and "candidate_erased" in src


def test_the_ownership_query_knows_about_erasure() -> None:
    from app.interviewer_scorecards import _OWNED_SQL

    assert "erasure_requests" in _OWNED_SQL and "candidate_erased" in _OWNED_SQL


@pytest.mark.parametrize("fn", ["save_draft", "open_correction"])
def test_scorecard_writes_refuse_an_erased_candidate(fn: str) -> None:
    import app.interviewer_scorecards as sc

    assert 'card.get("candidate_erased")' in inspect.getsource(getattr(sc, fn))


@pytest.mark.asyncio
async def test_a_draft_is_refused_for_an_erased_candidate(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.interviewer_scorecards as sc

    async def _owned(_db: object, **_kw: object) -> dict[str, Any]:
        return {"superseded_at": None, "status": "in_progress", "enrolment_status": "held",
                "candidate_erased": True, "round_id": uuid.uuid4()}

    monkeypatch.setattr(sc, "_load_owned", _owned)
    db = AsyncMock()
    with pytest.raises(sc.ScorecardError) as exc:
        await sc.save_draft(db, scorecard_id=uuid.uuid4(), interviewer_user_id=uuid.uuid4(),
                            company_id=uuid.uuid4(), scores=[], summary="about them",
                            meta=sc.RequestMeta())
    assert exc.value.status_code == 409
    db.execute.assert_not_awaited()


# ===========================================================================
# L1 / review — the database guards (source-level; behaviour is integration)
# ===========================================================================
def _a1_migration() -> str:
    return next(ALEMBIC.glob("*d2f4a6c8e0b1*")).read_text(encoding="utf-8")


def test_scores_cannot_be_moved_between_scorecards() -> None:
    assert "cannot be moved to another scorecard or criterion" in _a1_migration()


def test_withdrawn_assignments_are_delete_protected_and_the_allow_list_is_named_for_what_it_is(
) -> None:
    source = _a1_migration()
    assert "withdrawn assignment kept on record and cannot be deleted" in source
    assert "locked_keys" not in source and "mutable_after_submit" in source


def test_a_redacted_scorecard_takes_no_new_evidence() -> None:
    source = _a1_migration()
    assert "parent_redacted IS NOT NULL" in source and "NEW.evidence IS NOT NULL" in source


def test_redaction_covers_every_free_text_column() -> None:
    source = _a1_migration()
    for fragment in ("correction reason can only be redacted",
                     "withdrawal reason can only be redacted",
                     "its text cannot be written again"):
        assert fragment in source


# ===========================================================================
# L2 — writes share-lock the enrolment a decision locks
# ===========================================================================
def test_scorecard_writes_share_lock_the_enrolment() -> None:
    import app.interviewer_scorecards as sc

    assert "FOR SHARE OF e" in sc._OWNED_FOR_WRITE_SUFFIX
    assert "FOR SHARE OF e" in inspect.getsource(sc.assign)


# ===========================================================================
# L3 — a withdrawn correction does not erase its interviewer from the counts
# ===========================================================================
def test_counts_fall_back_to_the_original_when_its_correction_was_withdrawn() -> None:
    from app.interviewer_scorecards import summary_for_enrolments

    src = inspect.getsource(summary_for_enrolments)
    assert "nxt.id = s.superseded_by_id" in src and "nxt.status = 'withdrawn'" in src


# ===========================================================================
# L4 / L5 — decision reason taxonomy
# ===========================================================================
@pytest.mark.parametrize("label", ["C++", "éé", "A!", "!A", "??", "a", "9"])
def test_every_label_yields_a_code_the_database_accepts(label: str) -> None:
    from app.decision_reasons import _code_from_label

    assert re.fullmatch(r"[a-z][a-z0-9_]{1,63}", _code_from_label(label)), label


@pytest.mark.asyncio
async def test_a_lost_race_creating_a_reason_is_a_409_not_a_500() -> None:
    from sqlalchemy.exc import IntegrityError

    from app.decision_reasons import ReasonError, create_reason

    db = AsyncMock()
    db.add = MagicMock()
    db.scalar = AsyncMock(side_effect=[0, None, None])  # count, duplicate label, code taken

    async def _execute(stmt: object, *_a: object, **_kw: object) -> MagicMock:
        if "INSERT INTO decision_reasons (id" in str(stmt) and "80" in str(stmt):
            raise IntegrityError("insert", {}, Exception("uq_decision_reasons_company_code"))
        return MagicMock()

    db.execute = _execute
    with pytest.raises(ReasonError) as exc:
        await create_reason(db, company_id=uuid.uuid4(), actor=uuid.uuid4(),
                            label="Relocation", applies_to="rejected",
                            requires_explanation=False)
    assert exc.value.status_code == 409
    db.add.assert_not_called()


def _used_reason_db(uses: int) -> AsyncMock:
    row = {"code": "compensation", "label": "Compensation", "applies_to": "both",
           "requires_explanation": False, "active": True, "is_default": False}
    db = AsyncMock()
    db.add = MagicMock()
    db.execute = AsyncMock(return_value=_mapping_result(row))
    db.scalar = AsyncMock(return_value=uses)
    return db


@pytest.mark.parametrize(
    ("before", "after"),
    [("C++ skills", "C# skills"),
     ("वेतन अपेक्षा", "स्थान"),          # Hindi: an ASCII-only filter made both ""
     ("జీతం", "స్థానం"),                 # Telugu, likewise
     ("Compensation", "Compensation!")],
)
def test_the_meaning_check_is_not_fooled_by_symbols_or_scripts(before: str, after: str) -> None:
    from app.decision_reasons import _meaning

    assert _meaning(before) != _meaning(after)


def test_the_meaning_check_ignores_only_case_and_spacing() -> None:
    from app.decision_reasons import _meaning

    assert _meaning("  Skills   FIT ") == _meaning("skills fit")
    assert _meaning("ＳＫＩＬＬＳ") == _meaning("skills"), "NFKC folds full-width forms"


@pytest.mark.asyncio
async def test_usage_counts_decisions_on_people_with_no_application() -> None:
    """Those are recorded only on the audit row, never on a ledger."""
    from app.decision_reasons import ReasonError, update_reason

    db = _used_reason_db(1)
    with pytest.raises(ReasonError):
        await update_reason(db, company_id=uuid.uuid4(), actor=uuid.uuid4(),
                            code="compensation", active=None, label="Relocation")
    sql = str(db.scalar.await_args.args[0])
    assert "FROM stage_transitions" in sql and "FROM audit_log" in sql
    assert "applicant.decision.rejected" in sql


@pytest.mark.asyncio
async def test_a_used_reason_cannot_change_meaning() -> None:
    from app.decision_reasons import ReasonError, update_reason

    with pytest.raises(ReasonError) as exc:
        await update_reason(_used_reason_db(3), company_id=uuid.uuid4(), actor=uuid.uuid4(),
                            code="compensation", active=None, label="Relocation")
    assert exc.value.status_code == 409 and "Retire it" in exc.value.detail


@pytest.mark.asyncio
@pytest.mark.parametrize("label", ["compensation", "  COMPENSATION "])
async def test_a_used_reason_can_be_tidied(label: str) -> None:
    from app.decision_reasons import update_reason

    db = _used_reason_db(3)
    out = await update_reason(db, company_id=uuid.uuid4(), actor=uuid.uuid4(),
                              code="compensation", active=None, label=label)
    assert out["label"] == " ".join(label.split())
    db.scalar.assert_not_awaited()


# ===========================================================================
# L8 — kit list items are bounded while the request is parsed
# ===========================================================================
def test_an_oversized_kit_item_is_refused_at_the_request_layer() -> None:
    from app.routers.hr_scorecards import KitCriterionBody

    with pytest.raises(ValidationError):
        KitCriterionBody(competency_id="ps", probes=["x" * 301])
    KitCriterionBody(competency_id="ps", probes=["x" * 300])


# ===========================================================================
# Review — removing an interviewer goes through withdraw(), one card at a time
# ===========================================================================
def test_removing_staff_never_bulk_updates_scorecards() -> None:
    source = (APP / "routers/admin_hr.py").read_text(encoding="utf-8")
    assert "UPDATE interviewer_scorecards" not in source
    assert source.count("withdraw_open_for_interviewer(") == 2


@pytest.mark.asyncio
async def test_withdraw_open_for_interviewer_uses_withdraw_for_each(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.interviewer_scorecards as sc

    cards = [uuid.uuid4(), uuid.uuid4()]
    calls: list[dict[str, Any]] = []

    async def _withdraw(_db: object, **kw: Any) -> None:
        calls.append(kw)

    monkeypatch.setattr(sc, "withdraw", _withdraw)
    res = MagicMock()
    res.scalars.return_value.all.return_value = cards
    db = AsyncMock()
    db.execute = AsyncMock(return_value=res)
    n = await sc.withdraw_open_for_interviewer(
        db, company_id=uuid.uuid4(), interviewer_user_id=uuid.uuid4(), actor=uuid.uuid4(),
        reason="Removed", meta=sc.RequestMeta(),
    )
    assert n == 2 and [c["scorecard_id"] for c in calls] == cards
    assert all(c["removing_interviewer"] is True for c in calls)
