"""B1 smoke test — an opening's owner, target and closing date.

Standalone script, not a pytest module. Needs a THROWAWAY PostgreSQL at head (it
TRUNCATEs what it touches):

    cd services/data_gateway
    DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:55432/intants_smoke \
      python -m alembic upgrade head
    PYTHONPATH=".;../.." python tests/integration/smoke_group_b_ownership.py

The owner has to be someone who can actually act on the opening: an active HR
user at the same company. Before B1 the PATCH wrote any id it was given.
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


async def seed(f) -> dict:
    now = datetime.now(tz=UTC)
    s: dict = {k: uuid.uuid4() for k in (
        "company", "other_co", "hr", "colleague", "inactive", "candidate", "foreign_hr")}
    async with f() as db:
        for cid, slug in [(s["company"], "acme"), (s["other_co"], "globex")]:
            await db.execute(text(
                "INSERT INTO companies (id,name,slug,is_active,created_at,updated_at)"
                " VALUES (:i,:s,:s,true,:n,:n)"), {"i": cid, "s": slug, "n": now})
        hr_role = await db.scalar(text("SELECT id FROM roles WHERE name = 'hr_manager'"))
        for key, name, company, active, is_hr in [
            ("hr", "Hema HR", s["company"], True, True),
            ("colleague", "Kiran Colleague", s["company"], True, True),
            ("inactive", "Ina Inactive", s["company"], False, True),
            ("candidate", "Carl Candidate", s["company"], True, False),
            ("foreign_hr", "Greta Globex", s["other_co"], True, True),
        ]:
            await db.execute(text(
                "INSERT INTO users (id,email,full_name,password_hash,company_id,"
                " preferred_language,is_active,notify_login_email,must_change_password,"
                " created_at,updated_at)"
                " VALUES (:i,:e,:fn,'x',:c,'en',:a,false,false,:n,:n)"),
                {"i": s[key], "e": f"{key}@x.test", "fn": name, "c": company, "a": active,
                 "n": now})
            if is_hr:
                await db.execute(text(
                    "INSERT INTO user_roles (user_id, role_id, assigned_at) VALUES (:u,:r,:n)"),
                    {"u": s[key], "r": hr_role, "n": now})
        await db.commit()
    return s


async def main() -> None:
    eng = create_async_engine(URL)
    f = async_sessionmaker(eng, expire_on_commit=False)
    async with eng.begin() as c:
        await c.execute(text("TRUNCATE applicants, companies, users, job_requisitions,"
                             " enrolments, stage_transitions CASCADE"))
    s = await seed(f)

    from app.database import get_db_session
    from app.dependencies import get_hr_company
    from app.main import app

    async def _db():  # noqa: ANN202
        async with f() as session:
            yield session

    app.dependency_overrides[get_hr_company] = lambda: (s["hr"], s["company"])
    app.dependency_overrides[get_db_session] = _db
    ac = AsyncClient(transport=ASGITransport(app=app), base_url="http://t")
    soon = (datetime.now(tz=UTC) + timedelta(days=30)).isoformat()
    past = (datetime.now(tz=UTC) - timedelta(days=1)).isoformat()

    team = (await ac.get("/hr/team")).json()
    check("the team list is this company's active HR users only",
          sorted(m["full_name"] for m in team) == ["Hema HR", "Kiran Colleague"], str(team))

    r = await ac.post("/hr/requisitions", json={"title": "Welder"})
    check("an opening is owned by whoever creates it by default",
          r.status_code == 201 and r.json()["owner_user_id"] == str(s["hr"])
          and r.json()["owner_name"] == "Hema HR", r.text[:200])

    r = await ac.post("/hr/requisitions", json={
        "title": "Fitter", "owner_user_id": str(s["colleague"]), "target_hires": 3,
        "closes_at": soon})
    fitter = r.json() if r.status_code == 201 else {}
    check("an opening can be created with an owner, a target and a closing date",
          r.status_code == 201 and fitter["owner_name"] == "Kiran Colleague"
          and fitter["target_hires"] == 3 and fitter["closes_at"] is not None, r.text[:200])
    async with f() as db:
        creator = await db.scalar(text(
            "SELECT created_by_user_id FROM job_requisitions WHERE id = :i"),
            {"i": fitter.get("id")})
    check("…and it still records who created it", creator == s["hr"], str(creator))

    for key, why in [("foreign_hr", "at another company"), ("inactive", "deactivated"),
                     ("candidate", "without the HR role")]:
        r = await ac.post("/hr/requisitions", json={"title": f"Role {key}",
                                                    "owner_user_id": str(s[key])})
        check(f"an owner {why} is refused on create", r.status_code == 422, r.text[:120])
        r = await ac.patch(f"/hr/requisitions/{fitter['id']}",
                           json={"owner_user_id": str(s[key])})
        check(f"an owner {why} is refused on update", r.status_code == 422, r.text[:120])
    r = await ac.patch(f"/hr/requisitions/{fitter['id']}", json={"owner_user_id": str(uuid.uuid4())})
    check("an owner who does not exist is refused", r.status_code == 422, r.text[:120])

    r = await ac.patch(f"/hr/requisitions/{fitter['id']}",
                       json={"owner_user_id": str(s["hr"]), "target_hires": 5})
    check("the owner and target can be changed to valid values",
          r.status_code == 200 and r.json()["owner_name"] == "Hema HR"
          and r.json()["target_hires"] == 5, r.text[:200])
    r = await ac.patch(f"/hr/requisitions/{fitter['id']}", json={"owner_user_id": None})
    check("the owner can be cleared", r.status_code == 200 and r.json()["owner_user_id"] is None,
          r.text[:200])

    r = await ac.post("/hr/requisitions", json={"title": "Painter", "closes_at": past})
    check("a closing date in the past is refused on create", r.status_code == 422, r.text[:120])
    r = await ac.patch(f"/hr/requisitions/{fitter['id']}", json={"closes_at": past})
    check("…and on update", r.status_code == 422, r.text[:120])

    listed = {x["title"]: x for x in (await ac.get("/hr/requisitions")).json()}
    check("the list carries the owner's name too",
          listed.get("Welder", {}).get("owner_name") == "Hema HR", str(listed.get("Welder")))

    await ac.aclose()
    app.dependency_overrides.clear()
    await eng.dispose()
    print(f"\n{'='*64}\n  {len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("  FAILED:", ", ".join(FAIL))
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
