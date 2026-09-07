"""Group B API smoke — drives the HTTP layer against a live database.

Overrides only the auth dependency (there is no session to log in with here);
everything below it is the real router, the real service layer and real SQL.
This is the layer that caught the asyncpg AmbiguousParameterError on the
unfiltered list endpoint — a bug invisible to unit tests with a mocked DB.

    docker run -d --name intants-pgv -e POSTGRES_PASSWORD=postgres         -e POSTGRES_DB=intants_smoke -p 55432:5432 pgvector/pgvector:pg16
    cd services/data_gateway
    export DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:55432/intants_smoke
    python -m alembic downgrade c5e7a9b1d3f6
    PYTHONPATH=".;../.." python tests/integration/seed_pre_group_b.py
    python -m alembic upgrade head          # runs the backfill over the seed
    PYTHONPATH=".;../.." python tests/integration/smoke_group_b_api.py

Exits non-zero on any failed check.
"""
from __future__ import annotations

import asyncio
import uuid

from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database import get_db_session
from app.dependencies import get_hr_company
from app.main import app

URL = "postgresql+asyncpg://postgres:postgres@127.0.0.1:55432/intants_smoke"
PASS, FAIL = [], []


def check(label, cond, detail=""):
    (PASS if cond else FAIL).append(label)
    print(f"  {'PASS' if cond else 'FAIL'}  {label}{(' — ' + detail) if detail and not cond else ''}")


async def main() -> None:
    eng = create_async_engine(URL)
    factory = async_sessionmaker(eng, expire_on_commit=False)

    async with factory() as db:
        acme = await db.scalar(text("SELECT id FROM companies WHERE slug='acme'"))
        globex = await db.scalar(text("SELECT id FROM companies WHERE slug='globex'"))
        hr = await db.scalar(text("SELECT id FROM users WHERE email='hr@acme.test'"))

    async def _db_override():
        async with factory() as s:
            yield s

    app.dependency_overrides[get_db_session] = _db_override
    app.dependency_overrides[get_hr_company] = lambda: (hr, acme)

    tr = ASGITransport(app=app)
    async with AsyncClient(transport=tr, base_url="http://t") as c:
        # ── list ─────────────────────────────────────────────────────────
        r = await c.get("/hr/requisitions")
        check("GET /hr/requisitions -> 200", r.status_code == 200, r.text[:200])
        reqs = r.json()
        check("only this company's openings are listed", len(reqs) == 2, str(len(reqs)))
        py = next((x for x in reqs if x["title"] == "Python Developer"), None)
        check("rollup counts are present", py and py["total_enrolments"] == 5,
              str(py and py["total_enrolments"]))
        check("funnel breakdown returned", py and len(py["funnel"]) >= 2, str(py and py["funnel"]))
        check("backfilled rows are flagged as such", py and py["from_backfill"] is True)

        # ── review queue ─────────────────────────────────────────────────
        r = await c.get("/hr/requisitions/review")
        check("GET /hr/requisitions/review -> 200", r.status_code == 200, r.text[:200])
        rev = r.json()
        check("review lists the backfilled requisitions",
              len(rev["backfilled_requisitions"]) == 2, str(len(rev["backfilled_requisitions"])))
        pyrev = next(x for x in rev["backfilled_requisitions"] if x["title"] == "Python Developer")
        check("review shows how many spellings were folded together",
              pyrev["distinct_source_titles"] == 4, str(pyrev))

        # ── create / conflict ────────────────────────────────────────────
        r = await c.post("/hr/requisitions", json={"title": "Data Analyst", "level": "junior"})
        check("POST /hr/requisitions -> 201", r.status_code == 201, r.text[:200])
        new_id = r.json()["id"]
        check("a deliberately created opening is not flagged as backfill",
              r.json()["from_backfill"] is False)

        r = await c.post("/hr/requisitions", json={"title": "  data   ANALYST "})
        check("duplicate normalised title -> 409", r.status_code == 409, f"{r.status_code} {r.text[:120]}")

        r = await c.post("/hr/requisitions", json={"title": "   "})
        check("blank title -> 422", r.status_code == 422, str(r.status_code))

        # ── enrolments + move ────────────────────────────────────────────
        r = await c.get(f"/hr/requisitions/{py['id']}/enrolments")
        check("GET enrolments -> 200", r.status_code == 200, r.text[:200])
        enrs = r.json()
        check("enrolments ranked by ATS score",
              [e["ats_overall"] for e in enrs if e["ats_overall"]] ==
              sorted([e["ats_overall"] for e in enrs if e["ats_overall"]], reverse=True),
              str([e["ats_overall"] for e in enrs]))
        check("days_in_stage computed from the ledger",
              all(e["days_in_stage"] is not None for e in enrs), str(enrs[:1]))

        target = next(e for e in enrs if e["status"] == "new")
        r = await c.post(f"/hr/enrolments/{target['id']}/status",
                         json={"status": "shortlisted", "reason": "good fit"})
        check("POST enrolment status -> 200", r.status_code == 200, r.text[:200])
        check("status reflected in the response", r.json()["status"] == "shortlisted")

        r = await c.post(f"/hr/enrolments/{target['id']}/status", json={"status": "banana"})
        check("invalid status -> 422", r.status_code == 422, str(r.status_code))

        async with factory() as db:
            led = await db.scalar(text(
                "SELECT count(*) FROM stage_transitions WHERE enrolment_id=:e AND automated=false"),
                {"e": target["id"]})
            check("human move recorded as NOT automated", led == 1, f"count={led}")

        # ── closing must not touch candidates (D-05) ─────────────────────
        r = await c.post(f"/hr/requisitions/{py['id']}/status", json={"status": "closed"})
        check("POST requisition status -> 200", r.status_code == 200, r.text[:200])
        check("closing reports unresolved candidates rather than rejecting them",
              r.json()["awaiting_decision"] > 0, str(r.json()["awaiting_decision"]))
        async with factory() as db:
            rejected = await db.scalar(text(
                "SELECT count(*) FROM enrolments WHERE requisition_id=:r AND status='rejected'"),
                {"r": py["id"]})
            check("closing did not reject anyone", rejected == 1,
                  f"rejected={rejected} (only the pre-existing one)")

        # ── tenant isolation through the API ─────────────────────────────
        async with factory() as db:
            other = await db.scalar(text(
                "SELECT id FROM job_requisitions WHERE company_id=:g"), {"g": globex})
        r = await c.get(f"/hr/requisitions/{other}")
        check("another company's opening is 404, not 403", r.status_code == 404, str(r.status_code))
        r = await c.get(f"/hr/requisitions/{other}/enrolments")
        check("its enrolments are 404 too", r.status_code == 404, str(r.status_code))
        r = await c.get(f"/hr/requisitions/{uuid.uuid4()}")
        check("unknown id -> 404", r.status_code == 404, str(r.status_code))

        # ── patch clears the backfill flag ───────────────────────────────
        r = await c.patch(f"/hr/requisitions/{new_id}", json={"target_hires": 3})
        check("PATCH -> 200", r.status_code == 200, r.text[:200])
        check("target_hires stored", r.json()["target_hires"] == 3, str(r.json()))
        r = await c.patch(f"/hr/requisitions/{py['id']}", json={"title": "Python Engineer"})
        check("retitling confirms the grouping and clears the review flag",
              r.status_code == 200 and r.json()["from_backfill"] is False, r.text[:160])

    app.dependency_overrides.clear()
    await eng.dispose()
    print(f"\n{'=' * 64}\n  {len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("  FAILED:", ", ".join(FAIL))
    print("=" * 64)
    raise SystemExit(1 if FAIL else 0)


asyncio.run(main())
