"""E6 smoke test — per-opening watchers, against a real Postgres.

Standalone script, not a pytest module. Needs a THROWAWAY PostgreSQL at head
(it TRUNCATEs what it touches):

    cd services/data_gateway
    DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:55432/intants_smoke \
      python -m alembic upgrade head
    PYTHONPATH=".;../.." python tests/integration/smoke_group_e_watchers.py
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

URL = "postgresql+asyncpg://postgres:postgres@127.0.0.1:55432/intants_smoke"
PASS, FAIL = [], []


def check(label: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(label)
    print(f"  {'PASS' if cond else 'FAIL'}  {label}{(' — ' + detail) if detail and not cond else ''}")


async def main() -> None:  # noqa: PLR0915 — one linear script
    eng = create_async_engine(URL)
    f = async_sessionmaker(eng, expire_on_commit=False)
    now = datetime.now(tz=UTC)
    async with eng.begin() as c:
        await c.execute(text(
            "TRUNCATE applicants, companies, users, job_requisitions, enrolments,"
            " stage_transitions, workflows, workflow_rounds, round_criteria, round_results,"
            " reconciliation_state CASCADE"))

    cid, other_cid, hr = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    py, closed, designer, foreign = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    rounds: dict[str, uuid.UUID] = {}
    async with f() as db:
        for company, slug in ((cid, "acme"), (other_cid, "globex")):
            await db.execute(text(
                "INSERT INTO companies (id,name,slug,is_active,created_at,updated_at)"
                " VALUES (:i,:s,:s,true,:n,:n)"), {"i": company, "s": slug, "n": now})
        await db.execute(text(
            "INSERT INTO users (id,email,full_name,password_hash,company_id,preferred_language,"
            " is_active,notify_login_email,must_change_password,created_at,updated_at)"
            " VALUES (:i,'hr@acme.example.com','HR','x',:c,'en',true,false,false,:n,:n)"),
            {"i": hr, "c": cid, "n": now})
        for rid, company, title, status in ((py, cid, "Python Developer", "open"),
                                            (closed, cid, "Old Opening", "closed"),
                                            (designer, cid, "Designer", "open"),
                                            (foreign, other_cid, "Welder", "open")):
            await db.execute(text(
                "INSERT INTO job_requisitions (id,company_id,title,level,status,from_backfill,"
                " created_at,updated_at) VALUES (:i,:c,:t,'mid',:s,false,:n,:n)"),
                {"i": rid, "c": company, "t": title, "s": status, "n": now})

        async def workflow(rid: uuid.UUID, company: uuid.UUID, spec: list[tuple[str, str, int]]) -> uuid.UUID:
            wid = uuid.uuid4()
            await db.execute(text(
                "INSERT INTO workflows (id,company_id,requisition_id,version,status,"
                " auto_score_on_apply,auto_assign_first_round,auto_advance_rounds,"
                " reminders_enabled,hold_band,created_at,updated_at)"
                " VALUES (:i,:c,:r,1,'draft',true,true,true,true,10,:n,:n)"),
                {"i": wid, "c": company, "r": rid, "n": now})
            for pos, (key, kind, deadline) in enumerate(spec):
                rounds[key] = uuid.uuid4()
                await db.execute(text(
                    "INSERT INTO workflow_rounds (id,company_id,workflow_id,position,title,kind,"
                    " pass_threshold,deadline_days,created_at,updated_at)"
                    " VALUES (:i,:c,:w,:p,:t,:k,:th,:d,:n,:n)"),
                    {"i": rounds[key], "c": company, "w": wid, "p": pos, "t": key, "k": kind,
                     "th": None if kind == "human_review" else 60, "d": deadline, "n": now})
            # PH4-O6: a workflow must be walked through review (draft ->
            # in_review -> approved, by two different people) before the
            # database allows status -> 'published'.
            from tests.integration.seed_helpers import approve_for_publish

            await approve_for_publish(db, workflow_id=wid, company_id=company)
            await db.execute(text(
                "UPDATE workflows SET status='published', published_at=:n WHERE id=:i"),
                {"i": wid, "n": now})
            return wid

        wf_py = await workflow(py, cid, [("Technical Test", "mcq", 5),
                                         ("Final Review", "human_review", 5)])
        wf_closed = await workflow(closed, cid, [("Closed Test", "mcq", 5)])
        wf_foreign = await workflow(foreign, other_cid, [("Weld Test", "mcq", 5)])

        async def candidate(name: str, rid: uuid.UUID, status: str, round_key: str | None, *,
                            reached_days_ago: float, wf: uuid.UUID | None,
                            company: uuid.UUID = cid, touched_today: bool = True) -> None:
            aid, eid = uuid.uuid4(), uuid.uuid4()
            await db.execute(text(
                "INSERT INTO applicants (id,company_id,full_name,email,target_job_title,"
                " target_level,status,created_at,updated_at)"
                " VALUES (:i,:c,:fn,:em,'x','mid','new',:n,:n)"),
                {"i": aid, "c": company, "fn": name, "em": f"{name}@example.com", "n": now})
            created = now - timedelta(days=reached_days_ago + 1)
            await db.execute(text(
                "INSERT INTO enrolments (id,company_id,requisition_id,applicant_id,status,"
                " target_job_title,target_level,workflow_id,current_round_id,held_at,"
                " created_at,updated_at)"
                " VALUES (:i,:c,:r,:a,:s,'x','mid',:w,:cr,:ha,:ca,:ua)"),
                {"i": eid, "c": company, "r": rid, "a": aid, "s": status, "w": wf,
                 "cr": rounds[round_key] if round_key else None,
                 "ha": now if status == "held" else None, "ca": created,
                 # A rescore touched the row today; the ledger says otherwise.
                 "ua": now if touched_today else created})
            await db.execute(text(
                "INSERT INTO stage_transitions (company_id,enrolment_id,from_status,to_status,"
                " actor_user_id,automated,reason,occurred_at,to_round_id)"
                " VALUES (:c,:e,:f,:t,NULL,true,'smoke',:at,:tr)"),
                {"c": company, "e": eid, "f": "new" if status != "new" else None, "t": status,
                 "at": now - timedelta(days=reached_days_ago),
                 "tr": rounds[round_key] if round_key else None})

        # Python Developer / Technical Test (deadline 5): three stuck, one fresh.
        for n, d in (("Asha", 9), ("Bala", 8), ("Chen", 6)):
            await candidate(n, py, "shortlisted", "Technical Test", reached_days_ago=d, wf=wf_py)
        await candidate("Deepa", py, "shortlisted", "Technical Test", reached_days_ago=2, wf=wf_py)
        # Held on the same round: the decision backlog's, not a stall.
        await candidate("Held", py, "held", "Technical Test", reached_days_ago=20, wf=wf_py)
        # On a human review round past its deadline: also the backlog's.
        await candidate("Reviewer", py, "shortlisted", "Final Review", reached_days_ago=20, wf=wf_py)
        # A closed opening with someone stuck: finished, not neglected.
        await candidate("Closed", closed, "shortlisted", "Closed Test", reached_days_ago=30,
                        wf=wf_closed)
        # Never shortlisted, per opening, for the stalled-applicants rule.
        await candidate("Newbie", designer, "new", None, reached_days_ago=14, wf=None)
        await candidate("OldClosedApplicant", closed, "new", None, reached_days_ago=40, wf=None)
        # Another company's stall must never appear.
        await candidate("Foreign", foreign, "shortlisted", "Weld Test", reached_days_ago=30,
                        wf=wf_foreign, company=other_cid)
        await db.commit()

    from shared.agents import run_watchers

    from app.agents.watch_runner import gather_company_input, gather_round_stalls

    async with f() as db:
        data = await gather_company_input(db, str(cid))
        only_py = await gather_round_stalls(db, str(cid), str(py))
        only_designer = await gather_round_stalls(db, str(cid), str(designer))
    findings = run_watchers(data)
    stall = next((x for x in findings if x.watcher == "round_stalls"), None)

    check("one stalled round is found, in the live opening only",
          len(data.round_stalls) == 1 and data.round_stalls[0].requisition_title
          == "Python Developer", str(data.round_stalls))
    s = data.round_stalls[0] if data.round_stalls else None
    check("…counting the three past the round's 5-day deadline, from the ledger",
          s is not None and s.waiting == 3 and s.threshold_days == 5
          and 8.5 <= s.longest_days <= 9.5, str(s))
    check("…leaving out the held, the human review, the closed and the other company",
          s is not None and {n for _, n in s.candidates} == {"Asha", "Bala", "Chen"}, str(s))
    check("the alert names the opening and the round",
          stall is not None and stall.title == "Python Developer — Technical Test pipeline has stalled",
          str(stall.title if stall else None))
    check("…and says how many have waited past the threshold",
          stall is not None
          and stall.body.startswith("3 candidates have been waiting for more than 5 days"),
          str(stall.body if stall else None))
    check("the dashboard's query can narrow to one opening",
          len(only_py) == 1 and only_designer == [], f"{only_py} {only_designer}")

    stalled = [x for x in findings if x.watcher == "stalled_applicants"]
    check("stalled applicants are reported per opening, and not for a closed one",
          [x.title for x in stalled] == ["1 applicant(s) stalled over 10 days — Designer"],
          str([x.title for x in stalled]))
    check("…and nobody in a round or awaiting a person is double-counted there",
          all(n.name not in {"Asha", "Held", "Reviewer"} for n in data.stalled),
          str([n.name for n in data.stalled]))
    check("the decision backlog still covers the held and the reviewer",
          any(o.requisition_id == str(py) and o.awaiting_decision == 2 for o in data.openings),
          str([(o.title, o.awaiting_decision) for o in data.openings]))

    from app.database import get_db_session
    from app.dependencies import get_hr_company
    from app.main import app

    async def _db():  # noqa: ANN202
        async with f() as session:
            yield session

    app.dependency_overrides[get_hr_company] = lambda: (hr, cid)
    app.dependency_overrides[get_db_session] = _db
    ac = AsyncClient(transport=ASGITransport(app=app), base_url="http://t")
    one = (await ac.get("/hr/attention", params={"requisition_id": str(py)})).json()
    check("the attention panel can be read for one opening",
          one.get("total", 0) >= 1
          and all(any(c["kind"] == "job" and c["id"] == str(py) for c in i["citations"])
                  for i in one["items"])
          and any(i["watcher"] == "round_stalls" for i in one["items"]), str(one)[:300])
    r = await ac.get("/hr/attention", params={"requisition_id": str(foreign)})
    check("another company's opening is not found", r.status_code == 404, str(r.status_code))
    dash = (await ac.get(f"/hr/requisitions/{py}/dashboard")).json()
    check("the opening's dashboard shows the stall under Needs attention",
          any(i["title"] == "Python Developer — Technical Test pipeline has stalled"
              for i in dash.get("attention", [])), str(dash.get("attention"))[:300])

    await ac.aclose()
    app.dependency_overrides.clear()
    await eng.dispose()
    print(f"\n{'='*64}\n  {len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("  FAILED:", ", ".join(FAIL))
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
