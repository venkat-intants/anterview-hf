"""E6 — watchers scoped to the opening, and stalls named by opening and round.

The rules are tested in shared/agents/tests. These cover the SQL that feeds
them, the mapping, the per-opening attention filter, and the dashboard's use of
the same rule. ``smoke_group_e_watchers`` runs the SQL against Postgres.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from shared.agents import OpeningHealth, RoundStall, WatcherInput


def _sql(stmt: Any) -> str:
    return " ".join(str(stmt).split())


def test_stalled_applicants_are_per_opening_live_openings_and_ledger_time() -> None:
    from app.agents.watch_runner import STALLED_SQL

    sql = _sql(STALLED_SQL)
    assert "r.id AS requisition_id" in sql and "r.title AS requisition_title" in sql
    assert "r.status IN ('open', 'paused')" in sql
    assert "enrolment_state_since(e.id, e.created_at)" in sql
    assert "updated_at" not in sql
    # Not in a round (a round stall) and not waiting on a person (the backlog).
    assert "e.current_round_id IS NULL" in sql
    assert "NOT enrolment_awaits_human(e.status, e.current_round_id)" in sql


def test_the_funnel_population_is_open_or_paused_openings() -> None:
    """PH5-C2: the funnel watcher's counts now come from the governed metric
    layer (``app.metrics.compute.compute_funnel``, grouped by requisition),
    not a bespoke query — this used to assert ``FUNNEL_SQL``'s own GROUP BY
    text, which no longer exists. What still belongs to this module, and is
    still worth pinning, is which openings are in scope: open or paused, by
    id (never by title, which merged two openings that shared one)."""
    from app.agents.watch_runner import _OPEN_REQUISITIONS_SQL, _WATCHER_FUNNEL_METRICS

    sql = _sql(_OPEN_REQUISITIONS_SQL)
    assert "status IN ('open', 'paused')" in sql
    assert "id FROM job_requisitions" in sql
    # Neither metric may read a check-in flag — see
    # test_ph5_w1_metrics_definitions.test_watcher_funnel_metrics_are_checkin_safe
    # for why, and for the assertion against the real registry.
    assert _WATCHER_FUNNEL_METRICS == ("applications", "interviewed")


def test_round_stalls_use_the_rounds_deadline_and_skip_what_others_cover() -> None:
    from app.agents.watch_runner import ROUND_STALL_SQL

    sql = _sql(ROUND_STALL_SQL)
    assert "x.days > wr.deadline_days" in sql
    assert "t.to_round_id = e.current_round_id" in sql
    assert "e.status NOT IN ('hired', 'rejected', 'held')" in sql
    assert "wr.kind <> 'human_review'" in sql
    assert "r.status IN ('open', 'paused')" in sql
    # Company on every table, not only the first.
    assert sql.count("CAST(:cid AS uuid)") >= 3
    assert "CAST(:rid AS uuid) IS NULL OR e.requisition_id = CAST(:rid AS uuid)" in sql


@pytest.mark.asyncio
async def test_stall_rows_map_onto_the_rule_input() -> None:
    from app.agents.watch_runner import gather_round_stalls

    row = MagicMock(requisition_id=uuid.UUID(int=1), requisition_title="Python Developer",
                    round_id=uuid.UUID(int=2), round_title="Technical Test", threshold_days=5,
                    waiting=3, longest_days=8.4,
                    sample='[{"id": "a-1", "name": "Asha"}, {"id": "a-2", "name": "Bala"}]')
    db = MagicMock()
    result = MagicMock()
    result.all.return_value = [row]
    db.execute = AsyncMock(return_value=result)

    stalls = await gather_round_stalls(db, "co-1", "req-1")
    assert stalls[0].round_title == "Technical Test" and stalls[0].waiting == 3
    assert stalls[0].candidates == [("a-1", "Asha"), ("a-2", "Bala")]
    assert db.execute.call_args.args[1] == {"cid": "co-1", "rid": "req-1", "limit": 200}


@pytest.mark.asyncio
async def test_the_nightly_input_carries_round_stalls(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.agents.watch_runner as wr

    stall = RoundStall("req-1", "Python Developer", "r-1", "Technical Test", 5, 3, 8.0)

    async def _stalls(_db: object, _cid: str, _rid: str | None = None) -> list[RoundStall]:
        return [stall]

    monkeypatch.setattr(wr, "gather_round_stalls", _stalls)
    db = MagicMock()
    empty = MagicMock()
    empty.all.return_value = []
    db.execute = AsyncMock(return_value=empty)
    data = await wr.gather_company_input(db, "co-1")
    assert data.round_stalls == [stall]


# ===========================================================================
# The attention endpoint, for one opening
# ===========================================================================
def _input() -> WatcherInput:
    def opening(rid: str, title: str) -> OpeningHealth:
        return OpeningHealth(rid, title, awaiting_decision=0, longest_wait_days=0.0, held=0,
                             live_enrolments=4, has_published_workflow=False,
                             accepting_public_applications=True)

    return WatcherInput(
        company_id="co",
        openings=[opening(str(uuid.UUID(int=1)), "Python Developer"),
                  opening(str(uuid.UUID(int=2)), "Designer")],
        round_stalls=[RoundStall(str(uuid.UUID(int=1)), "Python Developer", "r-1",
                                 "Technical Test", 5, 3, 8.0)],
    )


@pytest.mark.asyncio
async def test_attention_can_be_read_for_one_opening(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.routers.hr_attention as hr_attention

    async def _gather(_db: object, _cid: str) -> WatcherInput:
        return _input()

    async def _owned(_db: object, _cid: object, _rid: object) -> dict:
        return {"id": _rid}

    monkeypatch.setattr(hr_attention, "gather_company_input", _gather)
    monkeypatch.setattr(hr_attention, "_owned", _owned)
    db = MagicMock()
    rid = uuid.UUID(int=1)

    everything = await hr_attention.get_attention((uuid.uuid4(), uuid.uuid4()), db)
    one = await hr_attention.get_attention((uuid.uuid4(), uuid.uuid4()), db, requisition_id=rid)

    assert {i.watcher for i in everything.items} == {"openings_without_workflow", "round_stalls"}
    assert len(everything.items) == 3
    assert one.total == 2
    assert all(any(c.kind == "job" and c.id == str(rid) for c in i.citations) for i in one.items)
    assert any(i.title == "Python Developer — Technical Test pipeline has stalled"
               for i in one.items)


@pytest.mark.asyncio
async def test_another_companys_opening_is_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.routers.hr_attention as hr_attention

    gathered: list[object] = []

    async def _gather(_db: object, cid: str) -> WatcherInput:
        gathered.append(cid)
        return _input()

    async def _not_ours(_db: object, _cid: object, _rid: object) -> dict:
        raise HTTPException(status_code=404, detail="Requisition not found.")

    monkeypatch.setattr(hr_attention, "gather_company_input", _gather)
    monkeypatch.setattr(hr_attention, "_owned", _not_ours)
    db = MagicMock()
    with pytest.raises(HTTPException) as exc:
        await hr_attention.get_attention((uuid.uuid4(), uuid.uuid4()), db,
                                         requisition_id=uuid.uuid4())
    assert exc.value.status_code == 404
    assert gathered == [], "nothing is gathered for an opening the caller does not own"


def test_the_dashboard_uses_the_same_stall_rule() -> None:
    import inspect

    from app.requisition_dashboard import gather_dashboard

    src = inspect.getsource(gather_dashboard)
    assert "gather_round_stalls(db, str(company_id), str(requisition_id))" in src
    assert "watch_round_stalls(" in src
