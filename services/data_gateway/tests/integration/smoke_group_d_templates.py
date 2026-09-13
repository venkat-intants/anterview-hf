"""D4 smoke test — starter templates, built against the role, through the API.

Standalone script, not a pytest module. Needs a THROWAWAY PostgreSQL at head:

    cd services/data_gateway
    DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:55432/intants_smoke \
      python -m alembic upgrade head
    PYTHONPATH=".;../.." python tests/integration/smoke_group_d_templates.py
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

URL = "postgresql+asyncpg://postgres:postgres@127.0.0.1:55432/intants_smoke"
PASS, FAIL = [], []


def check(label: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(label)
    print(f"  {'PASS' if cond else 'FAIL'}  {label}{(' — ' + detail) if detail and not cond else ''}")


async def main() -> None:
    eng = create_async_engine(URL)
    f = async_sessionmaker(eng, expire_on_commit=False)
    now = datetime.now(tz=UTC)
    async with eng.begin() as c:
        await c.execute(text(
            "TRUNCATE applicants, companies, users, job_requisitions, enrolments,"
            " stage_transitions, workflows, workflow_rounds, round_criteria CASCADE"))

    cid, uid, dev, nurse = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    async with f() as db:
        await db.execute(text(
            "INSERT INTO companies (id,name,slug,is_active,created_at,updated_at)"
            " VALUES (:i,'Acme','acme',true,:t,:t)"), {"i": cid, "t": now})
        await db.execute(text(
            "INSERT INTO users (id,email,full_name,password_hash,company_id,preferred_language,"
            " is_active,notify_login_email,must_change_password,created_at,updated_at)"
            " VALUES (:i,'hr@acme.test','HR','x',:c,'en',true,false,false,:t,:t)"),
            {"i": uid, "c": cid, "t": now})
        for rid, title in [(dev, "Python Developer"), (nurse, "Staff Nurse")]:
            await db.execute(text(
                "INSERT INTO job_requisitions (id,company_id,title,level,created_at,updated_at)"
                " VALUES (:i,:c,:t,'mid',:n,:n)"), {"i": rid, "c": cid, "t": title, "n": now})
        await db.commit()

    from app.database import get_db_session
    from app.dependencies import get_hr_company
    from app.main import app

    async def _db():  # noqa: ANN202
        async with f() as session:
            yield session

    app.dependency_overrides[get_hr_company] = lambda: (uid, cid)
    app.dependency_overrides[get_db_session] = _db
    ac = AsyncClient(transport=ASGITransport(app=app), base_url="http://t")

    # ── The picker: built for this role ─────────────────────────────────────
    dev_t = (await ac.get(f"/hr/requisitions/{dev}/workflow-templates")).json()
    nurse_t = (await ac.get(f"/hr/requisitions/{nurse}/workflow-templates")).json()
    check("three templates are offered",
          [t["key"] for t in dev_t] == ["technical", "non_technical", "interview_only"],
          str([t.get("key") for t in dev_t]))
    check("a software role is steered to Technical",
          next(t["key"] for t in dev_t if t["recommended"]) == "technical", str(dev_t))
    check("a nursing role is steered to Non-technical",
          next(t["key"] for t in nurse_t if t["recommended"]) == "non_technical", str(nurse_t))
    tech = dev_t[0]
    check("each round lists the role competencies it would assess",
          all(r["competencies"] for r in tech["rounds"] if r["kind"] != "human_review"),
          str(tech["rounds"]))

    # ── Creating from a template ─────────────────────────────────────────────
    r = await ac.post(f"/hr/requisitions/{dev}/workflows/from-template",
                      json={"template": "technical"})
    wf = r.json() if r.status_code == 201 else {}
    check("a template creates a draft", r.status_code == 201 and wf.get("status") == "draft",
          f"{r.status_code} {str(wf)[:200]}")
    rounds = wf.get("rounds", [])
    check("…with the Technical shape, in order",
          [x["kind"] for x in rounds] == ["mcq", "coding", "ai_interview", "human_review"],
          str([x.get("kind") for x in rounds]))
    check("…and competencies, thresholds, time limits and deadlines filled in",
          all(x["criteria"] for x in rounds if x["kind"] != "human_review")
          and rounds[0]["pass_threshold"] == 60 and rounds[0]["time_limit_seconds"] == 1800
          and all(x["deadline_days"] > 0 for x in rounds), str(rounds)[:400])

    async with f() as db:
        frozen = await db.scalar(text(
            "SELECT count(*) FROM round_criteria rc JOIN workflow_rounds wr ON wr.id = rc.round_id"
            " WHERE wr.workflow_id = :w AND rc.anchors IS NOT NULL"), {"w": wf.get("id")})
        settings = (await db.execute(text(
            "SELECT auto_score_on_apply, auto_assign_first_round, auto_advance_rounds,"
            " reminders_enabled FROM workflows WHERE id = :w"), {"w": wf.get("id")})).first()
    check("the full rubric was frozen onto the rounds, anchors included",
          (frozen or 0) > 0, f"frozen={frozen}")
    check("the automation settings are the recommended defaults",
          settings is not None and all(settings), str(settings))

    report = (await ac.get(f"/hr/workflows/{wf.get('id')}/validate")).json()
    errors = report.get("errors", [])
    check("the only blockers are the questions a template cannot attach",
          errors and all("needs questions" in e for e in errors), str(errors))
    gaps = [c for c in report.get("coverage", []) if c["times_assessed"] == 0]
    check("a fresh template leaves no competency unassessed", gaps == [], str(gaps))

    # ── Guards ──────────────────────────────────────────────────────────────
    again = await ac.post(f"/hr/requisitions/{dev}/workflows/from-template",
                          json={"template": "interview_only"})
    check("a second draft is refused while one exists", again.status_code == 409,
          str(again.status_code))
    bad = await ac.post(f"/hr/requisitions/{nurse}/workflows/from-template",
                        json={"template": "rocket_science"})
    check("an unknown template is refused", bad.status_code == 422, str(bad.status_code))
    other = await ac.get(f"/hr/requisitions/{uuid.uuid4()}/workflow-templates")
    check("another company's (or no) opening is a 404", other.status_code == 404,
          str(other.status_code))

    await ac.aclose()
    app.dependency_overrides.clear()
    await eng.dispose()
    print(f"\n{'='*64}\n  {len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("  FAILED:", ", ".join(FAIL))
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
