"""E3 smoke test — the company hiring board, against Postgres and the API.

Standalone script, not a pytest module. Needs a THROWAWAY PostgreSQL at head
(it TRUNCATEs what it touches):

    cd services/data_gateway
    DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:55432/intants_smoke \
      python -m alembic upgrade head
    PYTHONPATH=".;../.." python tests/integration/smoke_group_e_company_board.py
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests.integration.seed_helpers import approve_for_publish

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
            " upload_batches, upload_items CASCADE"))

    cid, other_cid, admin = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    ids = {k: uuid.uuid4() for k in ("on_track", "at_risk", "watch", "not_published", "closed",
                                     "foreign")}
    async with f() as db:
        for company, slug in ((cid, "acme"), (other_cid, "globex")):
            await db.execute(text(
                "INSERT INTO companies (id,name,slug,is_active,created_at,updated_at)"
                " VALUES (:i,:s,:s,true,:n,:n)"), {"i": company, "s": slug, "n": now})
        await db.execute(text(
            "INSERT INTO users (id,email,full_name,password_hash,company_id,preferred_language,"
            " is_active,notify_login_email,must_change_password,created_at,updated_at)"
            " VALUES (:i,'admin@acme.example.com','Company Admin','x',:c,'en',true,false,false,:n,:n)"),
            {"i": admin, "c": cid, "n": now})

        async def opening(key: str, *, title: str, status: str = "open", target: int | None,
                          closes_in: int | None, age: int, workflow: str | None,
                          company: uuid.UUID = cid, public: bool = False,
                          location: str | None = None) -> None:
            await db.execute(text(
        # approval_status: PH3-B2 put an approval gate in front of the
        # public surface, and an opening seeded straight into the table
        # defaults to draft — so every public-apply check here failed with
        # "not accepting applications". Seeded approved: what this smoke is
        # about is not the approval gate, which has its own tests. The
# decided-at goes with it: a CHECK constraint holds that a decided
# opening records when.
                "INSERT INTO job_requisitions (id,company_id,title,level,status,from_backfill,"
                " public_apply_enabled,approval_status,approval_decided_at,target_hires,"
                " closes_at,location,created_at,updated_at)"
                " VALUES (:i,:c,:t,'mid',:s,false,:p,'approved',:n,:th,:cl,:loc,:ca,:n)"),
                {"i": ids[key], "c": company, "t": title, "s": status, "p": public, "th": target,
                 "cl": now + timedelta(days=closes_in) if closes_in is not None else None,
                 "loc": location, "ca": now - timedelta(days=age), "n": now})
            if workflow:
                # PH4-O6: the database now refuses any INSERT that is not an
                # unreviewed draft, so a "published" workflow here is born a
                # draft, walked through review, and then published.
                wf_id = uuid.uuid4()
                await db.execute(text(
                    "INSERT INTO workflows (id,company_id,requisition_id,version,status,"
                    " auto_score_on_apply,auto_assign_first_round,"
                    " auto_advance_rounds,reminders_enabled,hold_band,created_at,updated_at)"
                    " VALUES (:i,:c,:r,1,'draft',true,true,true,true,10,:n,:n)"),
                    {"i": wf_id, "c": company, "r": ids[key], "n": now})
                if workflow == "published":
                    await approve_for_publish(db, workflow_id=wf_id, company_id=company)
                    await db.execute(text(
                        "UPDATE workflows SET status = 'published', published_at = :n"
                        " WHERE id = :i"),
                        {"i": wf_id, "n": now})

        await opening("on_track", title="Python Developer", target=2, closes_in=60, age=20,
                      workflow="published", location="Hyderabad")
        await opening("at_risk", title="Data Analyst", target=5, closes_in=10, age=30,
                      workflow="published")
        await opening("watch", title="Office Manager", target=None, closes_in=None, age=30,
                      workflow="published")
        await opening("not_published", title="Designer", target=1, closes_in=30, age=10,
                      workflow="draft", public=True)
        await opening("closed", title="Old Role", status="closed", target=1, closes_in=5,
                      age=40, workflow="published")
        await opening("foreign", title="Their Role", target=1, closes_in=5, age=40,
                      workflow="published", company=other_cid)

        async def candidate(key: str, status: str, *, moves: list[tuple[str, str, int]],
                            company: uuid.UUID = cid) -> None:
            aid, eid = uuid.uuid4(), uuid.uuid4()
            await db.execute(text(
                "INSERT INTO applicants (id,company_id,full_name,target_job_title,target_level,"
                " status,created_at,updated_at) VALUES (:i,:c,'x','x','mid','new',:n,:n)"),
                {"i": aid, "c": company, "n": now})
            await db.execute(text(
                "INSERT INTO enrolments (id,company_id,requisition_id,applicant_id,status,"
                " target_job_title,target_level,created_at,updated_at)"
                " VALUES (:i,:c,:r,:a,:s,'x','mid',:n,:n)"),
                {"i": eid, "c": company, "r": ids[key], "a": aid, "s": status,
                 "n": now - timedelta(days=25)})
            for frm, to, days_ago in moves:
                # PH4-O4: a move INTO hired/rejected needs a reason_code (and
                # its paired label) — the ledger trigger refuses one without.
                terminal = to in ("hired", "rejected") and frm != to
                await db.execute(text(
                    "INSERT INTO stage_transitions (company_id,enrolment_id,from_status,"
                    " to_status,actor_user_id,automated,reason,reason_code,reason_label,"
                    " occurred_at)"
                    " VALUES (:c,:e,:f,:t,NULL,true,'smoke',:rc,:rl,:at)"),
                    {"c": company, "e": eid, "f": frm, "t": to,
                     "rc": "other" if terminal else None, "rl": "Other" if terminal else None,
                     "at": now - timedelta(days=days_ago)})

        # Python Developer: six reached a decision in 20 days; 1 hire, 4 rejections
        # (its own ratio 20%) → 0.06 hires/day → the last hire in ~17 days, well
        # before a 60-day close.
        await candidate("on_track", "hired", moves=[("shortlisted", "interviewed", 15),
                                                    ("interviewed", "hired", 10)])
        for d in (18, 14, 12, 8):
            await candidate("on_track", "rejected", moves=[("shortlisted", "interviewed", d),
                                                           ("interviewed", "rejected", d - 1)])
        await candidate("on_track", "interviewed", moves=[("shortlisted", "interviewed", 3)])
        await candidate("on_track", "new", moves=[])
        await candidate("on_track", "new", moves=[])
        # Data Analyst: one decision in 30 days, target 5, closes in 10 days.
        await candidate("at_risk", "interviewed", moves=[("shortlisted", "interviewed", 12)])
        await candidate("at_risk", "shortlisted", moves=[("new", "shortlisted", 2)])
        # Designer: candidates arriving into a draft-only workflow.
        for _ in range(3):
            await candidate("not_published", "new", moves=[])
        await candidate("foreign", "interviewed", moves=[("shortlisted", "interviewed", 2)],
                        company=other_cid)
        await db.commit()

    from app.database import get_db_session
    from app.main import app
    from app.routers.admin_hr import get_company_admin_ctx

    async def _db():  # noqa: ANN202
        async with f() as session:
            yield session

    app.dependency_overrides[get_company_admin_ctx] = lambda: (admin, cid)
    app.dependency_overrides[get_db_session] = _db
    ac = AsyncClient(transport=ASGITransport(app=app), base_url="http://t")

    r = await ac.get("/admin/hiring-board")
    check("the board loads for the company super admin", r.status_code == 200,
          f"{r.status_code} {r.text[:200]}")
    board = r.json()
    rows = {o["title"]: o for o in board.get("openings", [])}
    check("it lists this company's open openings only — not closed, not another company's",
          set(rows) == {"Python Developer", "Data Analyst", "Office Manager", "Designer"},
          str(sorted(rows)))

    py = rows.get("Python Developer", {})
    check("each row carries applied, in play, hired against target and awaiting decision",
          py.get("applied") == 8 and py.get("in_play") == 3 and py.get("hired") == 1
          and py.get("target_hires") == 2 and py.get("awaiting_decision") == 1
          and py.get("location") == "Hyderabad", str(py))
    check("…and its workflow version and state",
          py.get("workflow_state") == "published" and py.get("published_version") == 1, str(py))

    bands = {t: o["health"]["band"] for t, o in rows.items()}
    check("on track: projected to fill before the closing date, from its own hire ratio",
          bands.get("Python Developer") == "on_track"
          and py["health"]["assumed_hire_ratio"] is False
          and py["health"]["projected_fill_date"] is not None, str(py.get("health")))
    check("at risk: the projected fill date is after the closing date",
          bands.get("Data Analyst") == "at_risk"
          and "after the closing date" in rows["Data Analyst"]["health"]["reason"],
          str(rows.get("Data Analyst", {}).get("health")))
    check("watch: nothing to project against", bands.get("Office Manager") == "watch",
          str(rows.get("Office Manager", {}).get("health")))
    check("not published: a separate state, saying it is taking applications",
          bands.get("Designer") == "not_published"
          and rows["Designer"]["workflow_state"] == "draft"
          and "taking applications" in rows["Designer"]["health"]["reason"],
          str(rows.get("Designer")))
    check("worst health first, with a count per band",
          [o["health"]["band"] for o in board["openings"]]
          == ["not_published", "at_risk", "watch", "on_track"]
          and board["summary"] == {"not_published": 1, "at_risk": 1, "watch": 1, "on_track": 1},
          str(board.get("summary")))

    r = await ac.get(f"/admin/requisitions/{ids['on_track']}/dashboard")
    dash = r.json() if r.status_code == 200 else {}
    check("the super admin can open an opening's dashboard, read-only",
          r.status_code == 200 and dash.get("progress", {}).get("applications") == 8,
          f"{r.status_code} {str(dash)[:160]}")
    r = await ac.get(f"/admin/requisitions/{ids['foreign']}/dashboard")
    check("…but not another company's", r.status_code == 404, str(r.status_code))
    r = await ac.post(f"/admin/requisitions/{ids['on_track']}/dashboard")
    check("…and there is nothing to write through", r.status_code == 405, str(r.status_code))

    await ac.aclose()
    app.dependency_overrides.clear()
    await eng.dispose()
    print(f"\n{'='*64}\n  {len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("  FAILED:", ", ".join(FAIL))
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
