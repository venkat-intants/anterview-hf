"""C9 smoke test — the two workflow settings that were stored and never read.

Standalone script, not a pytest module. Needs a THROWAWAY PostgreSQL at head:

    cd services/data_gateway
    DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:55432/intants_smoke \
      python -m alembic upgrade head
    PYTHONPATH=".;../.." python tests/integration/smoke_group_c_settings.py

``auto_score_on_apply`` off must actually stop the spend, on both paths that
score (the upload and the reconciler). ``shortlist_ats_threshold`` must reach a
person: candidates at or above it are offered for confirmation on the attention
panel, and nothing advances them (D-05).
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
SCORE = {"overall": 71, "breakdown": {}, "strengths": [], "concerns": [],
         "recommendation": "consider", "summary": "ok"}


def check(label: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(label)
    print(f"  {'PASS' if cond else 'FAIL'}  {label}{(' — ' + detail) if detail and not cond else ''}")


async def seed(f) -> dict:
    now = datetime.now(tz=UTC)
    s: dict = {k: uuid.uuid4() for k in ("company", "hr", "scored", "unscored", "wf_on", "wf_off")}
    async with f() as db:
        await db.execute(text(
            "INSERT INTO companies (id,name,slug,is_active,created_at,updated_at)"
            " VALUES (:i,'acme','acme',true,:n,:n)"), {"i": s["company"], "n": now})
        await db.execute(text(
            "INSERT INTO users (id,email,full_name,password_hash,company_id,preferred_language,"
            " is_active,notify_login_email,must_change_password,created_at,updated_at)"
            " VALUES (:i,'hr@acme.test','HR','x',:c,'en',true,false,false,:n,:n)"),
            {"i": s["hr"], "c": s["company"], "n": now})
        for rid, title in [(s["scored"], "Scored Role"), (s["unscored"], "Quiet Role")]:
            await db.execute(text(
                "INSERT INTO job_requisitions (id,company_id,title,level,status,from_backfill,"
                " created_at,updated_at) VALUES (:i,:c,:t,'mid','open',false,:n,:n)"),
                {"i": rid, "c": s["company"], "t": title, "n": now})
        # Two published workflows: one scores arrivals and sets a shortlist bar
        # of 7/10, the other scores nothing.
        for wf, rid, auto, thr in [(s["wf_on"], s["scored"], True, 7),
                                   (s["wf_off"], s["unscored"], False, None)]:
            await db.execute(text(
                "INSERT INTO workflows (id,company_id,requisition_id,version,status,"
                " auto_score_on_apply,auto_assign_first_round,auto_advance_rounds,"
                " reminders_enabled,shortlist_ats_threshold,hold_band,created_at,updated_at,"
                " published_at) VALUES (:i,:c,:r,1,'published',:a,true,true,true,:t,10,:n,:n,:n)"),
                {"i": wf, "c": s["company"], "r": rid, "a": auto, "t": thr, "n": now})
        await db.commit()
    return s


async def main() -> None:
    eng = create_async_engine(URL)
    f = async_sessionmaker(eng, expire_on_commit=False)
    async with eng.begin() as c:
        await c.execute(text(
            "TRUNCATE applicants, companies, users, job_requisitions, enrolments,"
            " stage_transitions, workflows, workflow_rounds, reconciliation_state CASCADE"))
    s = await seed(f)

    import app.routers.hr_applicants as hra
    from app.database import get_db_session
    from app.dependencies import get_hr_company
    from app.main import app

    scored_calls: list[str] = []

    async def _score(**kw: object) -> dict:
        scored_calls.append(str(kw.get("job_title")))
        return SCORE

    async def _noop(*_: object, **__: object) -> None:
        return None

    async def _text(_raw: bytes) -> str:
        return "cv text"

    hra.score_resume_remote = _score  # type: ignore[assignment]
    hra._upload_to_s3 = _noop  # type: ignore[assignment]
    hra._extract_pdf_text = _text  # type: ignore[assignment]
    hra._embed_applicant = _noop  # type: ignore[assignment]

    async def _db():  # noqa: ANN202
        async with f() as session:
            yield session

    app.dependency_overrides[get_hr_company] = lambda: (s["hr"], s["company"])
    app.dependency_overrides[get_db_session] = _db
    ac = AsyncClient(transport=ASGITransport(app=app), base_url="http://t")
    pdf = ("cv.pdf", b"%PDF-1.4", "application/pdf")

    # ── 1. auto_score_on_apply on the upload path ───────────────────────────
    r1 = await ac.post("/hr/applicants", files={"file": pdf},
                       data={"full_name": "Anita", "target_job_title": "x",
                             "requisition_id": str(s["scored"])})
    r2 = await ac.post("/hr/applicants", files={"file": pdf},
                       data={"full_name": "Bilal", "target_job_title": "x",
                             "requisition_id": str(s["unscored"])})
    async with f() as db:
        rows = {r[0]: r[1] for r in (await db.execute(text(
            "SELECT a.full_name, e.ats_overall FROM applicants a"
            "  JOIN enrolments e ON e.applicant_id = a.id"))).all()}
    check("both uploads succeed", r1.status_code == 201 and r2.status_code == 201,
          f"{r1.status_code} {r2.status_code}")
    check("the opening that scores on arrival is scored", rows.get("Anita") == 71, str(rows))
    check("the opening that turned scoring off is not, and pays for nothing",
          rows.get("Bilal") is None and scored_calls == ["Scored Role"], str(scored_calls))

    # ── 2. …and the reconciler does not score it later either ───────────────
    import app.reconciliation as rec

    rec.score_resume_remote = _score  # type: ignore[assignment]
    async with f() as db:
        await rec._score_pass(db, rec.PassResult())
    async with f() as db:
        bilal = await db.scalar(text(
            "SELECT e.ats_overall FROM enrolments e JOIN applicants a ON a.id = e.applicant_id"
            " WHERE a.full_name = 'Bilal'"))
    check("the reconciler leaves that opening alone too",
          bilal is None and scored_calls == ["Scored Role"], f"{bilal} {scored_calls}")

    # ── 3. The shortlist bar reaches a person ───────────────────────────────
    async with f() as db:
        # Anita scored 71 (7.1/10) — at the bar. A second candidate lands under it.
        await db.execute(text(
            "INSERT INTO applicants (id,company_id,created_by_user_id,full_name,"
            " target_job_title,target_level,resume_text,status,created_at,updated_at)"
            " VALUES (:i,:c,:u,'Chandra','x','mid','cv','new',now(),now())"),
            {"i": (low := uuid.uuid4()), "c": s["company"], "u": s["hr"]})
        await db.execute(text(
            "INSERT INTO enrolments (id,company_id,requisition_id,applicant_id,status,"
            " target_job_title,ats_overall,created_at,updated_at)"
            " VALUES (:i,:c,:r,:a,'new','x',52,now(),now())"),
            {"i": uuid.uuid4(), "c": s["company"], "r": s["scored"], "a": low})
        await db.commit()

    panel = (await ac.get("/hr/attention")).json()
    items = {i["watcher"]: i for i in panel["items"]}
    ready = items.get("ready_to_shortlist")
    check("candidates over the bar are surfaced for confirmation",
          ready is not None and "1 candidate(s) meet the bar for Scored Role" in ready["title"],
          str(sorted(items)))
    check("…as information, not as an action taken",
          ready is not None and ready["severity"] == "info" and "7/10" in ready["body"],
          str(ready))
    async with f() as db:
        statuses = [r[0] for r in (await db.execute(text(
            "SELECT status FROM enrolments WHERE requisition_id = :r"),
            {"r": s["scored"]})).all()]
        moves = await db.scalar(text(
            "SELECT count(*) FROM stage_transitions WHERE to_status = 'shortlisted'"))
    check("nobody was shortlisted by the score (D-05)",
          set(statuses) == {"new"} and moves == 0, f"{statuses} moves={moves}")

    # The opening with no bar set says nothing at all.
    check("an opening with no bar is not mentioned",
          ready is not None and "Quiet Role" not in ready["body"], str(ready))

    await ac.aclose()
    app.dependency_overrides.clear()
    await eng.dispose()
    print(f"\n{'='*64}\n  {len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("  FAILED:", ", ".join(FAIL))
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
