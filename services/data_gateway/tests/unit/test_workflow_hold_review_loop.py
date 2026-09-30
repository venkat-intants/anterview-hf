"""The manual-hold loop, end to end: HR is told, and "release" moves them on.

The per-round ``auto_advance`` toggle shipped the hold itself and neither half of
the loop around it, which made the setting a dead end in production:

* ``_hold`` notified nobody. A held candidate appeared in ``decision_queue`` and
  waited there until an HR manager happened to open that screen. Tolerable while
  holding was an edge case (a near-miss score); not tolerable once "Hold for my
  review" is a per-round setting HR chooses on purpose.
* ``release_hold`` cleared ``held_at`` and recorded a transition without touching
  ``current_round_id``, so a released candidate came back to the pipeline still
  sitting on the round they had already finished — nothing assigned, no link
  minted, and no further event coming to move them.

Both were invisible to the suite because every test asserted the hold, and none
asked what happened next.
"""

from __future__ import annotations

import inspect
import uuid
from typing import Any
from unittest.mock import AsyncMock

import pytest

import app.workflow_runner as wr


def _db() -> AsyncMock:
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=None)
    db.execute = AsyncMock()
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    return db


def _enrolment(**over: Any) -> dict[str, Any]:
    base = {
        "id": uuid.uuid4(), "company_id": uuid.uuid4(), "applicant_id": uuid.uuid4(),
        "requisition_id": uuid.uuid4(), "status": "shortlisted",
        "workflow_id": uuid.uuid4(), "current_round_id": uuid.uuid4(),
        "target_job_title": "Bench Fitter", "full_name": "Asha Rao",
        "email": "asha@example.test",
    }
    return {**base, **over}


def _round(**over: Any) -> dict[str, Any]:
    base = {
        "id": uuid.uuid4(), "workflow_id": uuid.uuid4(), "position": 1,
        "title": "Trade test", "kind": "mcq", "pass_threshold": 60.0, "deadline_days": 7,
        "on_pass_next_round_id": None, "exam_round_id": uuid.uuid4(),
        "on_fail_next_round_id": None, "fast_track_min_percent": None,
        "on_fast_track_next_round_id": None, "auto_advance": False,
    }
    return {**base, **over}


def _workflow(**over: Any) -> dict[str, Any]:
    base = {"id": uuid.uuid4(), "auto_advance_rounds": True, "hold_band": 5,
            "created_by_user_id": uuid.uuid4()}
    return {**base, **over}


@pytest.fixture
def notified(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Every notification the runner stages, in order."""
    sent: list[dict[str, Any]] = []

    async def _create(_db: object, **kw: Any) -> bool:
        sent.append(kw)
        return True

    monkeypatch.setattr(wr, "create_notification", _create)
    return sent


# ---------------------------------------------------------------------------
# A hold summons a person
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_a_hold_tells_the_workflows_owner(
    monkeypatch: pytest.MonkeyPatch, notified: list[dict[str, Any]]
) -> None:
    monkeypatch.setattr(wr, "record_transition", AsyncMock(return_value="shortlisted"))
    enrolment, round_, workflow = _enrolment(), _round(), _workflow()

    out = await wr._hold(_db(), enrolment, "Trade test: passed — held for review",
                         round_=round_, workflow=workflow)

    assert out.action == "held"
    assert len(notified) == 1
    note = notified[0]
    assert note["user_id"] == workflow["created_by_user_id"]
    assert "Asha Rao" in note["title"]
    # The reason has to travel with it: "why has this candidate not moved?" is
    # the only question the notification exists to answer.
    assert "held for review" in note["body"]
    assert note["link"] == "/hr/requisitions"


@pytest.mark.asyncio
async def test_the_same_hold_is_announced_once(
    monkeypatch: pytest.MonkeyPatch, notified: list[dict[str, Any]]
) -> None:
    """A re-grade re-holds the same candidate at the same round. Announcing that
    again would train HR to ignore the feed, so the dedupe key names the pair —
    and the database, not this producer's bookkeeping, enforces it."""
    monkeypatch.setattr(wr, "record_transition", AsyncMock(return_value="shortlisted"))
    enrolment, round_, workflow = _enrolment(), _round(), _workflow()

    await wr._hold(_db(), enrolment, "first", round_=round_, workflow=workflow)
    await wr._hold(_db(), enrolment, "after a re-grade", round_=round_, workflow=workflow)

    keys = {n["dedupe_key"] for n in notified}
    assert len(keys) == 1
    assert str(enrolment["id"]) in next(iter(keys))
    assert str(round_["id"]) in next(iter(keys))


@pytest.mark.asyncio
async def test_a_hold_at_a_different_round_is_a_new_announcement(
    monkeypatch: pytest.MonkeyPatch, notified: list[dict[str, Any]]
) -> None:
    monkeypatch.setattr(wr, "record_transition", AsyncMock(return_value="shortlisted"))
    enrolment, workflow = _enrolment(), _workflow()

    await wr._hold(_db(), enrolment, "held at one", round_=_round(), workflow=workflow)
    await wr._hold(_db(), enrolment, "held at two", round_=_round(title="Panel"),
                   workflow=workflow)

    assert len({n["dedupe_key"] for n in notified}) == 2


@pytest.mark.asyncio
async def test_a_hold_still_happens_when_there_is_nobody_to_tell(
    monkeypatch: pytest.MonkeyPatch, notified: list[dict[str, Any]]
) -> None:
    """A workflow whose creator's account is gone must not strand candidates.
    create_notification already no-ops on a null user; the hold itself is what
    must not depend on it."""
    monkeypatch.setattr(wr, "record_transition", AsyncMock(return_value="shortlisted"))

    out = await wr._hold(_db(), _enrolment(), "held", round_=_round(),
                         workflow=_workflow(created_by_user_id=None))

    assert out.action == "held"
    assert notified[0]["user_id"] is None  # passed through; the helper drops it


@pytest.mark.asyncio
async def test_every_hold_in_the_runner_names_its_round_and_workflow() -> None:
    """A hold that cannot say where it happened cannot notify, and a caller that
    forgets the arguments fails silently — the hold still works. Asserted
    against the source because that silence is the whole risk."""
    source = inspect.getsource(wr)
    calls = [
        line for line in source.splitlines()
        if "_hold(" in line and "async def _hold" not in line and "def _hold" not in line
    ]
    assert calls, "no _hold call sites found — has the function been renamed?"
    # Each call spans more than one line, so check the call's whole text.
    for marker in ("round_=round_", "workflow=workflow"):
        assert source.count(marker) >= 4, f"a _hold call site is missing {marker}"


# ---------------------------------------------------------------------------
# A release moves them on
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_releasing_a_hold_advances_the_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The defect, stated directly: the release used to stop at clearing the
    flag, leaving the candidate parked on a finished round."""
    next_id = uuid.uuid4()
    round_ = _round(on_pass_next_round_id=next_id)
    enrolment = _enrolment(status="held", current_round_id=round_["id"])

    advanced: list[dict[str, Any]] = []

    async def _advance(_db: object, **kw: Any) -> wr.RunnerOutcome:
        advanced.append(kw)
        return wr.RunnerOutcome(action="advanced", enrolment_id=str(enrolment["id"]),
                                reason="pass")

    monkeypatch.setattr(wr, "_load_enrolment", AsyncMock(return_value=enrolment))
    monkeypatch.setattr(wr, "_load_round", AsyncMock(return_value=round_))
    monkeypatch.setattr(wr, "_load_workflow", AsyncMock(return_value=_workflow()))
    monkeypatch.setattr(wr, "record_transition", AsyncMock(return_value="held"))
    monkeypatch.setattr(wr, "_advance", _advance)

    out = await wr.release_hold(_db(), enrolment_id=enrolment["id"],
                                actor_user_id=uuid.uuid4(), reason="references fine")

    assert out.action == "advanced"
    assert len(advanced) == 1, "the release did not move the candidate"
    assert advanced[0]["route"].next_round_id == str(next_id)
    assert advanced[0]["route"].branch == "pass"
    # The reviewer's words reach the round-move ledger too, not just the status
    # transition — "who decided this and why" has to survive on both.
    assert "references fine" in advanced[0]["why"]


@pytest.mark.asyncio
async def test_the_released_status_is_what_advance_carries_forward(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_advance copies the candidate's CURRENT status onto the next round. Handed
    the un-updated row it would re-record "held" on someone just released, and
    the pipeline would show them held on a round they had moved past."""
    round_ = _round(on_pass_next_round_id=uuid.uuid4())
    enrolment = _enrolment(status="held", current_round_id=round_["id"])
    seen: list[str] = []

    async def _advance(_db: object, **kw: Any) -> wr.RunnerOutcome:
        seen.append(kw["enrolment"]["status"])
        return wr.RunnerOutcome(action="advanced")

    monkeypatch.setattr(wr, "_load_enrolment", AsyncMock(return_value=enrolment))
    monkeypatch.setattr(wr, "_load_round", AsyncMock(return_value=round_))
    monkeypatch.setattr(wr, "_load_workflow", AsyncMock(return_value=_workflow()))
    monkeypatch.setattr(wr, "record_transition", AsyncMock(return_value="held"))
    monkeypatch.setattr(wr, "_advance", _advance)

    await wr.release_hold(_db(), enrolment_id=enrolment["id"], actor_user_id=uuid.uuid4(),
                          to_status="interviewed")

    assert seen == ["interviewed"]
    assert enrolment["status"] == "held", "the loaded row must not be mutated in place"


@pytest.mark.asyncio
async def test_a_failing_candidate_is_released_along_the_pass_branch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Releasing a below-threshold candidate IS a reviewer overriding the
    threshold (D-05). Sending them down the fail branch would re-apply the
    machine's verdict to a decision a person has just overruled."""
    pass_to, fail_to = uuid.uuid4(), uuid.uuid4()
    round_ = _round(on_pass_next_round_id=pass_to, on_fail_next_round_id=fail_to)
    enrolment = _enrolment(status="held", current_round_id=round_["id"])
    routes: list[Any] = []

    async def _advance(_db: object, **kw: Any) -> wr.RunnerOutcome:
        routes.append(kw["route"])
        return wr.RunnerOutcome(action="advanced")

    monkeypatch.setattr(wr, "_load_enrolment", AsyncMock(return_value=enrolment))
    monkeypatch.setattr(wr, "_load_round", AsyncMock(return_value=round_))
    monkeypatch.setattr(wr, "_load_workflow", AsyncMock(return_value=_workflow()))
    monkeypatch.setattr(wr, "record_transition", AsyncMock(return_value="held"))
    monkeypatch.setattr(wr, "_advance", _advance)

    await wr.release_hold(_db(), enrolment_id=enrolment["id"], actor_user_id=uuid.uuid4())

    assert routes[0].next_round_id == str(pass_to)


@pytest.mark.asyncio
async def test_a_release_never_fast_tracks(monkeypatch: pytest.MonkeyPatch) -> None:
    """The fast-track is earned by a score. A release has no percentage to
    compare, so it must take the ordinary pass branch rather than skipping
    rounds on a reviewer's behalf."""
    pass_to, fast_to = uuid.uuid4(), uuid.uuid4()
    round_ = _round(on_pass_next_round_id=pass_to, on_fast_track_next_round_id=fast_to,
                    fast_track_min_percent=90)
    enrolment = _enrolment(status="held", current_round_id=round_["id"])
    routes: list[Any] = []

    async def _advance(_db: object, **kw: Any) -> wr.RunnerOutcome:
        routes.append(kw["route"])
        return wr.RunnerOutcome(action="advanced")

    monkeypatch.setattr(wr, "_load_enrolment", AsyncMock(return_value=enrolment))
    monkeypatch.setattr(wr, "_load_round", AsyncMock(return_value=round_))
    monkeypatch.setattr(wr, "_load_workflow", AsyncMock(return_value=_workflow()))
    monkeypatch.setattr(wr, "record_transition", AsyncMock(return_value="held"))
    monkeypatch.setattr(wr, "_advance", _advance)

    await wr.release_hold(_db(), enrolment_id=enrolment["id"], actor_user_id=uuid.uuid4())

    assert routes[0].next_round_id == str(pass_to)
    assert routes[0].branch == "pass"


@pytest.mark.asyncio
async def test_a_release_with_no_current_round_just_lifts_the_hold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Held after the last round: there is nowhere to advance to, and the
    candidate belongs in the final-decision queue."""
    enrolment = _enrolment(status="held", current_round_id=None)
    advanced: list[object] = []

    async def _advance(_db: object, **kw: Any) -> wr.RunnerOutcome:
        advanced.append(kw)
        return wr.RunnerOutcome(action="advanced")

    monkeypatch.setattr(wr, "_load_enrolment", AsyncMock(return_value=enrolment))
    monkeypatch.setattr(wr, "record_transition", AsyncMock(return_value="held"))
    monkeypatch.setattr(wr, "_advance", _advance)

    out = await wr.release_hold(_db(), enrolment_id=enrolment["id"],
                                actor_user_id=uuid.uuid4())

    assert out.action == "advanced"
    assert advanced == []
    assert "final decision" in (out.reason or "")


@pytest.mark.asyncio
async def test_a_deleted_round_lifts_the_hold_and_says_nobody_moved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Refusing the release would leave the candidate held forever. Claiming a
    move that did not happen would be worse — HR would stop looking for them."""
    enrolment = _enrolment(status="held")
    monkeypatch.setattr(wr, "_load_enrolment", AsyncMock(return_value=enrolment))
    monkeypatch.setattr(wr, "_load_round", AsyncMock(return_value=None))
    monkeypatch.setattr(wr, "_load_workflow", AsyncMock(return_value=_workflow()))
    monkeypatch.setattr(wr, "record_transition", AsyncMock(return_value="held"))

    out = await wr.release_hold(_db(), enrolment_id=enrolment["id"],
                                actor_user_id=uuid.uuid4())

    assert out.action == "advanced"
    assert "did not move" in (out.reason or "")


@pytest.mark.asyncio
async def test_a_release_still_refuses_to_decide_anything(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unchanged by any of the above, and the reason the whole path exists: a
    release continues a candidate and can never end one. Moving people on is now
    real, which makes it worth re-asserting that rejecting still is not."""
    monkeypatch.setattr(wr, "_load_enrolment", AsyncMock(return_value=_enrolment()))

    for status in ("rejected", "hired"):
        out = await wr.release_hold(_db(), enrolment_id=uuid.uuid4(),
                                    actor_user_id=uuid.uuid4(), to_status=status)
        assert out.action == "noop"
        assert "final decision" in (out.reason or "")


# ---------------------------------------------------------------------------
# The three queries that load a round must not drift apart
# ---------------------------------------------------------------------------
def test_every_query_that_loads_a_round_selects_the_same_columns() -> None:
    """A round loaded WITHOUT ``auto_advance`` inherits the workflow default
    silently — ``auto_advance_for`` treats a missing key and a NULL identically,
    deliberately, so a row from a query written before the column cannot raise
    mid-process. The price of that safety is that a column forgotten in one of
    three near-identical SELECTs is invisible: nothing errors, the candidate just
    takes the wrong branch.

    ``load_rounds`` legitimately omits ``workflow_id`` — its rows are hashed
    verbatim into ``workflow_fingerprint``, and a result recorded against one
    fingerprint says nothing about another, so adding a column there would
    invalidate every fingerprint already recorded against a live version. That
    one difference is allowed; every other must match.
    """
    import re

    from app import workflows

    def columns(source: str) -> set[str]:
        body = re.search(r'"SELECT (.*?)"\s*\n\s*"\s+FROM workflow_rounds', source, re.S)
        assert body, "could not find the round SELECT — has the query been rewritten?"
        # The SQL is built from adjacent string literals; drop the quoting and
        # the leading padding, then split on commas.
        flat = re.sub(r'"\s*\n\s*"', " ", body.group(1))
        return {c.strip() for c in flat.split(",") if c.strip()}

    runner_src = inspect.getsource(wr)
    loaders = [
        columns(inspect.getsource(wr._load_round)),
        columns(inspect.getsource(wr._first_round)),
    ]
    assert loaders[0] == loaders[1], "the runner's two round loaders disagree"
    assert "auto_advance" in loaders[0], "the runner cannot resolve the per-round override"

    from_workflows = columns(inspect.getsource(workflows.load_rounds))
    assert from_workflows | {"workflow_id"} == loaders[0], (
        "load_rounds and the runner's loaders differ by more than workflow_id: "
        f"{from_workflows ^ loaders[0]}"
    )
    # And no third copy has appeared since.
    assert runner_src.count("FROM workflow_rounds WHERE") == 2


@pytest.mark.asyncio
async def test_a_task_round_the_runner_assigns_carries_its_time_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Found by the column-drift test above, and live: the runner's loaders did
    not select ``time_limit_seconds``, and ``job_tasks.issue`` reads it off the
    round dict its CALLER passes (``round_.get("time_limit_seconds")``).

    So a job-simulation or portfolio round assigned automatically was UNTIMED,
    while the same round assigned from the HR screen — which loads the round
    through ``job_tasks._round``, a query that does select the column — was
    timed. ``.get`` meant nothing raised; the limit was simply gone, and
    ``base_time_limit_seconds`` was stored NULL, which is also what an
    accommodation's extra time is computed from.
    """
    round_ = _round(kind="job_simulation", exam_round_id=None, time_limit_seconds=3600)
    issued: list[dict[str, Any]] = []

    async def _issue(_db: object, **kw: Any) -> None:
        issued.append(kw["round_"])

    monkeypatch.setattr(wr.job_tasks, "issue", _issue)
    monkeypatch.setattr(wr, "record_round_move", AsyncMock(return_value=True))

    await wr._move_to_round(
        _db(), enrolment=_enrolment(), round_=round_, workflow=_workflow()
    )

    assert issued, "the runner did not assign the task round"
    assert issued[0]["time_limit_seconds"] == 3600
