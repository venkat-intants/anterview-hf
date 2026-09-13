"""D2/D3 smoke test — changing a round's type through the API, and coverage.

Standalone script, not a pytest module. Needs a THROWAWAY PostgreSQL at head:

    cd services/data_gateway
    DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:55432/intants_smoke \
      python -m alembic upgrade head
    PYTHONPATH=".;../.." python tests/integration/smoke_group_d_round_type.py

The round model's PATCH schema used to omit ``kind``, so the API silently dropped
a type change even though update_round supported one. And when a type did
change, the old type's settings stayed on the row.
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
            " stage_transitions, workflows, workflow_rounds, round_criteria,"
            " exams, exam_rounds CASCADE"))

    cid, uid, rid = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    exam, er = uuid.uuid4(), uuid.uuid4()
    async with f() as db:
        await db.execute(text(
            "INSERT INTO companies (id,name,slug,is_active,created_at,updated_at)"
            " VALUES (:i,'Acme','acme',true,:t,:t)"), {"i": cid, "t": now})
        await db.execute(text(
            "INSERT INTO users (id,email,full_name,password_hash,company_id,preferred_language,"
            " is_active,notify_login_email,must_change_password,created_at,updated_at)"
            " VALUES (:i,'hr@acme.test','HR','x',:c,'en',true,false,false,:t,:t)"),
            {"i": uid, "c": cid, "t": now})
        await db.execute(text(
            "INSERT INTO job_requisitions (id,company_id,title,level,created_at,updated_at)"
            " VALUES (:i,:c,'Python Developer','mid',:t,:t)"), {"i": rid, "c": cid, "t": now})
        await db.execute(text(
            "INSERT INTO exams (id,company_id,title,created_by_user_id,created_at,updated_at)"
            " VALUES (:i,:c,'Screen',:u,:t,:t)"), {"i": exam, "c": cid, "u": uid, "t": now})
        await db.execute(text(
            "INSERT INTO exam_rounds (id,exam_id,company_id,round_number,title,position,status,"
            " created_at,updated_at) VALUES (:i,:e,:c,1,'Aptitude',0,'published',:t,:t)"),
            {"i": er, "e": exam, "c": cid, "t": now})
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

    # A templated draft: Aptitude (MCQ) → Coding → AI Interview → Human Review.
    wf = (await ac.post(f"/hr/requisitions/{rid}/workflows/from-template",
                        json={"template": "technical"})).json()
    wf_id = wf["id"]
    aptitude = wf["rounds"][0]
    await ac.patch(f"/hr/workflows/{wf_id}/rounds/{aptitude['id']}",
                   json={"exam_round_id": str(er)})

    # ── Changing the type goes through, and clears what no longer applies ──
    r = await ac.patch(f"/hr/workflows/{wf_id}/rounds/{aptitude['id']}",
                       json={"kind": "human_review"})
    body = r.json() if r.status_code == 200 else {}
    changed = next((x for x in body.get("rounds", []) if x["id"] == aptitude["id"]), {})
    check("the API accepts a type change", r.status_code == 200 and changed.get("kind") ==
          "human_review", f"{r.status_code} {changed}")
    check("…and clears the exam, threshold and time limit a human review cannot have",
          changed.get("exam_round_id") is None and changed.get("pass_threshold") is None
          and changed.get("time_limit_seconds") is None, str(changed))
    check("…while keeping its competencies, now the reviewer's checklist",
          len(changed.get("criteria", [])) > 0, str(changed.get("criteria")))

    r = await ac.patch(f"/hr/workflows/{wf_id}/rounds/{aptitude['id']}",
                       json={"kind": "mcq"})
    back = next((x for x in r.json().get("rounds", []) if x["id"] == aptitude["id"]), {})
    report = (await ac.get(f"/hr/workflows/{wf_id}/validate")).json()
    check("turning it back into a test invents no threshold — validation asks for one",
          back.get("pass_threshold") is None
          and any("Aptitude: needs an advance threshold" in e for e in report["errors"]),
          f"{back} {report['errors']}")

    bad = await ac.patch(f"/hr/workflows/{wf_id}/rounds/{aptitude['id']}",
                         json={"kind": "essay"})
    check("an unknown type is refused", bad.status_code == 422, str(bad.status_code))

    # ── Coverage as a share of the role's weight ────────────────────────────
    share = report.get("weighted_coverage")
    check("validation reports the share of the role's weight the rounds assess",
          isinstance(share, float) and 0 < share <= 1, str(share))
    async with f() as db:
        await db.execute(text(
            "DELETE FROM round_criteria WHERE round_id IN"
            " (SELECT id FROM workflow_rounds WHERE workflow_id = :w)"), {"w": wf_id})
        await db.commit()
    empty = (await ac.get(f"/hr/workflows/{wf_id}/validate")).json()
    check("with no competencies selected, coverage is zero rather than missing",
          empty.get("weighted_coverage") == 0.0, str(empty.get("weighted_coverage")))

    await ac.aclose()
    app.dependency_overrides.clear()
    await eng.dispose()
    print(f"\n{'='*64}\n  {len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("  FAILED:", ", ".join(FAIL))
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
