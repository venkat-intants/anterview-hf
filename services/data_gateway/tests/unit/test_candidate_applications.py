"""Unit tests for the candidate's own applications view.

Two things are being defended here, and only one of them is ordinary.

The ordinary one is presentation: internal statuses must reach the candidate as
the words we chose, round positions must be counted from one, and a status this
build has never seen must degrade to something neutral rather than leaking a
raw enum or raising.

The one that matters is what the response does NOT contain. This router reads
enrolments — rows that also carry ATS scores, recommendations and summaries —
and hands a subset to the person those scores are about. A future edit that
widens the SELECT or the response model would be a DPDP problem, not a styling
one, so the exclusion is asserted rather than left to review.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest


def _row(**kw: object) -> MagicMock:
    row = MagicMock()
    defaults = {
        "id": uuid.uuid4(),
        "status": "new",
        "target_job_title": "Backend Engineer",
        "created_at": datetime(2026, 9, 1, 10, 0, tzinfo=UTC),
        "updated_at": datetime(2026, 9, 2, 10, 0, tzinfo=UTC),
        "company_name": "Acme Test Co",
        "requisition_title": "Backend Engineer",
        "round_title": None,
        "round_kind": None,
        "round_position": None,
        "total_rounds": None,
    }
    for k, v in {**defaults, **kw}.items():
        setattr(row, k, v)
    return row


def _event(status: str = "new", automated: bool = True) -> MagicMock:
    e = MagicMock()
    e.to_status = status
    e.occurred_at = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)
    e.automated = automated
    return e


def _db(rows: list, events: list | None = None) -> AsyncMock:
    """A session that answers the application query, then the history query.

    Two results, not one repeated: the detail endpoint runs both, and handing
    it application rows as stage transitions made a passing test out of a
    response that could never be built.
    """
    def _res(items: list) -> MagicMock:
        r = MagicMock()
        r.all = MagicMock(return_value=items)
        r.first = MagicMock(return_value=items[0] if items else None)
        return r

    db = AsyncMock()
    queue = [_res(rows), _res(events or [])]

    async def _execute(*_a: object, **_k: object) -> MagicMock:
        return queue.pop(0) if queue else _res([])

    db.execute = AsyncMock(side_effect=_execute)
    return db


def _user(uid: str | None = None) -> MagicMock:
    u = MagicMock()
    u.user_id = uid or str(uuid.uuid4())
    return u


# ===========================================================================
# The candidate-facing vocabulary
# ===========================================================================
@pytest.mark.parametrize(
    ("status", "stage"),
    [
        ("new", "Application received"),
        ("shortlisted", "Shortlisted"),
        ("interviewed", "Interview complete"),
        ("hired", "Selected"),
        ("rejected", "Not progressing"),
    ],
)
def test_each_status_has_candidate_wording(status: str, stage: str) -> None:
    from app.routers.candidate_applications import _to_out

    assert _to_out(_row(status=status))["stage"] == stage


def test_held_reads_as_under_review() -> None:
    """D-05, in the one place a candidate can see it.

    ``held`` means below a round threshold. A threshold decides advancement and
    never the outcome — a person decides that, and at the moment the candidate
    reads this page nobody has. "Under review" is the accurate statement;
    reporting the internal word would imply a decision that has not been made.
    """
    out = _to_out_of("held")
    assert out["stage"] == "Under review"
    assert "held" not in out["stage"].lower()
    assert "held" not in out["next_step"].lower()
    assert out["closed"] is False


def _to_out_of(status: str) -> dict:
    from app.routers.candidate_applications import _to_out

    return _to_out(_row(status=status))


def test_every_internal_status_is_covered() -> None:
    """No status may reach a candidate untranslated.

    Coupled to ``requisitions.VALID_STATUSES`` on purpose: adding a status
    there without wording for it should fail here rather than render an enum to
    the person it describes.
    """
    from app.requisitions import VALID_STATUSES
    from app.routers.candidate_applications import _NEXT_STEPS, _STAGE_LABELS

    assert set(VALID_STATUSES) <= set(_STAGE_LABELS)
    assert set(VALID_STATUSES) <= set(_NEXT_STEPS)


def test_an_unknown_status_degrades_quietly() -> None:
    from app.routers.candidate_applications import _to_out

    out = _to_out(_row(status="quantum_superposition"))
    assert out["stage"] == "In progress"
    assert out["next_step"]
    assert "quantum" not in out["next_step"]


@pytest.mark.parametrize(("status", "closed"), [
    ("new", False), ("shortlisted", False), ("held", False),
    ("interviewed", False), ("hired", True), ("rejected", True),
])
def test_only_terminal_statuses_are_closed(status: str, closed: bool) -> None:
    assert _to_out_of(status)["closed"] is closed


# ===========================================================================
# Progress
# ===========================================================================
def test_round_position_is_counted_from_one() -> None:
    """position is 0-based in the table; "Round 0 of 4" is not a sentence."""
    from app.routers.candidate_applications import _to_out

    out = _to_out(_row(round_title="Coding Round", round_kind="coding",
                       round_position=1, total_rounds=4))
    assert out["round_number"] == 2
    assert out["total_rounds"] == 4
    assert out["current_round_title"] == "Coding Round"


def test_no_round_yet_is_null_not_zero() -> None:
    """The common case: HR is still reviewing, no round assigned.

    Zero would render as "Round 0", and a falsy total would make the frontend's
    "Round n of m" line appear with a blank in it.
    """
    from app.routers.candidate_applications import _to_out

    out = _to_out(_row(round_position=None, total_rounds=0))
    assert out["round_number"] is None
    assert out["total_rounds"] is None
    assert out["current_round_title"] is None


def test_the_requisitions_current_title_wins() -> None:
    """A renamed opening should read as it is named today, not as it was."""
    from app.routers.candidate_applications import _to_out

    out = _to_out(_row(requisition_title="Senior Backend Engineer",
                       target_job_title="Backend Engineer"))
    assert out["job_title"] == "Senior Backend Engineer"


def test_the_frozen_title_is_the_fallback() -> None:
    from app.routers.candidate_applications import _to_out

    out = _to_out(_row(requisition_title=None, target_job_title="Backend Engineer"))
    assert out["job_title"] == "Backend Engineer"


# ===========================================================================
# What must never be in the response
# ===========================================================================
def test_no_evaluation_of_the_candidate_is_returned() -> None:
    from app.routers.candidate_applications import _to_out

    out = _to_out(_row(status="held"))
    for forbidden in (
        "ats_overall", "ats_breakdown", "ats_strengths", "ats_concerns",
        "ats_recommendation", "ats_summary", "pass_threshold", "score",
        "held_reason", "reason", "rationale",
    ):
        assert forbidden not in out, f"{forbidden} must not reach the candidate"


def test_the_response_model_cannot_carry_a_score() -> None:
    """The model is the contract; widening it is the change to argue about."""
    from app.routers.candidate_applications import ApplicationOut

    fields = set(ApplicationOut.model_fields)
    assert not {f for f in fields if "ats" in f or "score" in f or "threshold" in f}


def test_the_query_never_selects_an_ats_column() -> None:
    """Belt and braces: not selected, so it cannot be leaked by a later edit
    that adds a field to the model and forgets why it was absent."""
    from app.routers.candidate_applications import _LIST_SQL

    lowered = _LIST_SQL.lower()
    assert "ats_" not in lowered
    assert "pass_threshold" not in lowered
    assert "held_reason" not in lowered


def test_stage_history_excludes_the_hr_reason() -> None:
    """``stage_transitions.reason`` is where a frank internal note would sit."""
    import inspect

    from app.routers.candidate_applications import StageEventOut, get_my_application

    assert "reason" not in set(StageEventOut.model_fields)
    src = inspect.getsource(get_my_application)
    assert "reason" not in src.split("SELECT")[1].split("FROM")[0]


# ===========================================================================
# Scope — the router must not be pointable at anyone else
# ===========================================================================
def test_no_endpoint_accepts_an_identity_parameter() -> None:
    """Same rule as onboarding.py, and it matters more here: these rows belong
    to a company's hiring process, not just to a practice plan."""
    import inspect

    from app.routers import candidate_applications as mod

    for fn in (mod.list_my_applications, mod.get_my_application):
        params = set(inspect.signature(fn).parameters)
        assert not params & {"user_id", "applicant_id", "company_id", "enrolment_id"}


@pytest.mark.asyncio
async def test_the_list_query_is_scoped_to_the_caller() -> None:
    from app.routers.candidate_applications import list_my_applications

    uid = uuid.uuid4()
    db = _db([_row()])
    await list_my_applications(_user(str(uid)), db)
    params = db.execute.await_args.args[1]
    assert params["uid"] == uid


@pytest.mark.asyncio
async def test_detail_is_scoped_by_user_as_well_as_id() -> None:
    """The id alone must not be enough — that would make it an oracle."""
    from app.routers.candidate_applications import _LIST_SQL, get_my_application

    uid, eid = uuid.uuid4(), uuid.uuid4()
    db = _db([_row(id=eid)])
    await get_my_application(eid, _user(str(uid)), db)
    sql = str(db.execute.await_args_list[0].args[0])
    assert "a.user_id = :uid" in sql
    assert "e.id = :eid" in sql
    assert "a.user_id = :uid" in _LIST_SQL


@pytest.mark.asyncio
async def test_someone_elses_application_is_a_404() -> None:
    from fastapi import HTTPException

    from app.routers.candidate_applications import get_my_application

    with pytest.raises(HTTPException) as exc:
        await get_my_application(uuid.uuid4(), _user(), _db([]))
    # Not 403: a 403 would confirm the id names a real application.
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_a_practice_only_account_gets_an_empty_list() -> None:
    """Not an error. Most accounts on the platform have applied to nothing."""
    from app.routers.candidate_applications import list_my_applications

    assert await list_my_applications(_user(), _db([])) == []


# ===========================================================================
# Stage history
# ===========================================================================
@pytest.mark.asyncio
async def test_history_marks_who_moved_the_candidate() -> None:
    """Answers "did a person look at this, or did the system move me?" — the
    question someone waiting actually has, and the one the ledger's
    ``automated`` column exists to answer."""
    from app.routers.candidate_applications import get_my_application

    eid = uuid.uuid4()
    db = _db(
        [_row(id=eid, status="shortlisted")],
        [_event("new", automated=True), _event("shortlisted", automated=False)],
    )
    out = await get_my_application(eid, _user(), db)
    assert [(e.stage, e.by_a_person) for e in out.history] == [
        ("Application received", False),
        ("Shortlisted", True),
    ]


@pytest.mark.asyncio
async def test_history_translates_stages_too() -> None:
    """A raw status must not leak through the timeline either."""
    from app.routers.candidate_applications import get_my_application

    eid = uuid.uuid4()
    db = _db([_row(id=eid, status="held")], [_event("held")])
    out = await get_my_application(eid, _user(), db)
    assert out.history[0].stage == "Under review"


# ===========================================================================
# Open roles — the discovery half of the bridge
# ===========================================================================
def test_open_roles_show_only_what_is_already_public() -> None:
    """Cross-tenant, unlike the careers board, and that needs a reason.

    The reason is that these are the same rows the board already serves to the
    whole internet — every one gated on an explicit public_apply_enabled opt-in.
    The feed adds the finding, not the exposure. A predicate missing here would
    put a private opening in front of a stranger with an account.
    """
    from app.routers.candidate_applications import _OPEN_ROLES_SQL

    for gate in ("public_apply_enabled", "status = 'open'", "r.deleted_at IS NULL"):
        assert gate in _OPEN_ROLES_SQL
    assert "closes_at IS NULL OR r.closes_at > :now" in _OPEN_ROLES_SQL


def test_open_roles_are_not_ranked() -> None:
    """Newest first and nothing else. The moment a feed ranks openings it is
    making a commercial decision, and that is not one to arrive at as a side
    effect of sorting."""
    from app.routers.candidate_applications import _OPEN_ROLES_SQL

    order = _OPEN_ROLES_SQL.split("ORDER BY")[1]
    assert "r.created_at DESC" in order
    for ranked in ("score", "relevance", "featured", "promoted", "RANDOM"):
        assert ranked.lower() not in order.lower()


def test_open_roles_flag_the_ones_this_candidate_already_applied_to() -> None:
    """Offering somebody a role they applied to last week, with no sign that
    they did, is worse than not listing it."""
    from app.routers.candidate_applications import _OPEN_ROLES_SQL

    assert "already_applied" in _OPEN_ROLES_SQL
    # Scoped to this user, not to anyone who ever applied.
    applied_clause = _OPEN_ROLES_SQL.split("already_applied")[0].split("EXISTS")[1]
    assert "a.user_id = :uid" in applied_clause


def test_open_roles_hide_a_company_that_is_no_longer_active() -> None:
    from app.routers.candidate_applications import _OPEN_ROLES_SQL

    assert "c.is_active" in _OPEN_ROLES_SQL
    assert "c.deleted_at IS NULL" in _OPEN_ROLES_SQL


def test_an_open_role_cannot_carry_a_withheld_salary() -> None:
    from app.routers.candidate_applications import OpenRole

    assert "salary_visible" not in OpenRole.model_fields


@pytest.mark.asyncio
async def test_a_withheld_salary_does_not_reach_the_feed() -> None:
    from app.routers.candidate_applications import list_open_roles

    row = {
        "id": uuid.uuid4(), "title": "Senior Python Engineer", "level": "senior",
        "department": "Engineering", "location": "Bengaluru",
        "employment_type": "full_time", "experience_min_years": 4,
        "experience_max_years": 9, "required_skills": ["Python"],
        "salary_min": 2_400_000, "salary_max": 3_600_000, "salary_currency": "INR",
        "salary_visible": False, "created_at": datetime(2026, 9, 4, tzinfo=UTC),
        "company_name": "Acme Test Co", "company_slug": "acme-test",
        "already_applied": False,
    }
    db = AsyncMock()
    result, mapped = MagicMock(), MagicMock()
    mapped.all = MagicMock(return_value=[row])
    result.mappings = MagicMock(return_value=mapped)
    db.execute = AsyncMock(return_value=result)

    out = await list_open_roles(_user(), db)
    assert out[0].salary_min is None
    assert "2400000" not in out[0].model_dump_json()


def test_the_feed_takes_no_identity_parameter() -> None:
    """Same rule as the rest of this router: it cannot be pointed at somebody
    else's account."""
    import inspect

    from app.routers.candidate_applications import list_open_roles

    params = set(inspect.signature(list_open_roles).parameters)
    assert not params & {"user_id", "company_id", "slug", "applicant_id"}
