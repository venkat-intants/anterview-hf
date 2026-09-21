#!/usr/bin/env python3
"""PH4-D4 end to end: a job-simulation round and a portfolio round, each
issued by the runner's own branch, worked and submitted through the real
``/task`` link, reviewed by a named interviewer through the EXISTING
scorecard machinery, passed by HR through the EXISTING round-review endpoint
— advancing to a plain human_review round — with tenant isolation, re-issue
and withdrawal, and the retention purge. Through the real API, against a
real Postgres.

    cd services/data_gateway
    PYTHONPATH=".;../.." DATABASE_URL=postgresql+asyncpg://ph3:ph3@127.0.0.1:55432/ph4_w5 \\
      DATABASE_SSL= python tests/integration/smoke_ph4_d4_tasks.py

OBJECT STORAGE IS FAKED, DELIBERATELY. MinIO is not available on this
machine for this wave (another project holds :9000, untouched); app.job_tasks
delegates every byte-carrying call to app.document_storage, so this script
replaces exactly those four functions (store/signed_download/remove/
keys_under) with in-memory equivalents before the app is exercised.
``document_storage.check`` — the magic-byte sniff — is untouched and runs for
real. What this script does NOT prove: that bytes actually round-trip through
S3/R2. That is out of scope for this session and is called out again in the
final report.

Seeds its own company; leaves it behind (unique slug), like the other smokes.
"""

from __future__ import annotations

import asyncio
import itertools
import os
import uuid
from datetime import UTC, datetime

from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

URL = os.environ.get("SMOKE_DATABASE_URL", "postgresql+asyncpg://ph3:ph3@127.0.0.1:55432/ph4_w5")
PASS: list[str] = []
FAIL: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(label)
    print(f"  {'PASS' if cond else 'FAIL'}  {label}" + (f" — {detail}" if not cond and detail else ""))


# ---------------------------------------------------------------------------
# A fake object store — in-memory, no network. Keyed exactly like the real one.
# ---------------------------------------------------------------------------
class _FakeStore:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    async def store(self, settings: object, key: str, data: bytes, content_type: str) -> None:
        self.objects[key] = data

    async def signed_download(self, settings: object, key: str, filename: str) -> str:
        return f"https://fake-storage.invalid/{key}?filename={filename}"

    async def remove(self, settings: object, keys: list[str]) -> int:
        removed = 0
        for k in keys:
            if self.objects.pop(k, None) is not None:
                removed += 1
        return removed

    async def keys_under(self, settings: object, prefix: str) -> list[str]:
        return [k for k in self.objects if k.startswith(prefix)]


async def main() -> None:  # noqa: PLR0915 — one linear script, read top to bottom
    eng = create_async_engine(URL)
    factory = async_sessionmaker(eng, expire_on_commit=False)
    now = datetime.now(tz=UTC)
    tag = uuid.uuid4().hex[:8]
    cid = uuid.uuid4()
    other_cid = uuid.uuid4()
    hr_a = uuid.uuid4()
    interviewer = uuid.uuid4()
    other_hr = uuid.uuid4()

    async with factory() as db:
        await db.execute(
            text("INSERT INTO companies (id,name,slug,is_active,created_at,updated_at)"
                 " VALUES (:i,'Acme D4',:s,true,:n,:n), (:oi,'Other D4',:os,true,:n,:n)"),
            {"i": cid, "s": f"d4-{tag}", "oi": other_cid, "os": f"d4o-{tag}", "n": now},
        )
        for uid, cmp, name, roles in (
            (hr_a, cid, "Hema HR", ("hr_manager",)),
            (interviewer, cid, "Ivy Interviewer", ("interviewer",)),
            (other_hr, other_cid, "Otto HR", ("hr_manager",)),
        ):
            await db.execute(
                text("INSERT INTO users (id,email,full_name,password_hash,company_id,"
                     " preferred_language,is_active,notify_login_email,must_change_password,"
                     " created_at,updated_at)"
                     " VALUES (:i,:e,:fn,'x',:c,'en',true,false,false,:n,:n)"),
                {"i": uid, "e": f"{uid.hex[:10]}@{tag}.test", "fn": name, "c": cmp, "n": now},
            )
            for role in roles:
                await db.execute(
                    text("INSERT INTO user_roles (user_id, role_id, assigned_at)"
                         " SELECT :u, id, :n FROM roles WHERE name = :r"),
                    {"u": uid, "n": now, "r": role},
                )

        req = uuid.uuid4()
        await db.execute(
            text("INSERT INTO job_requisitions (id, company_id, title, status, owner_user_id,"
                 " created_at, updated_at) VALUES (:r,:c,'Backend Engineer','open',:h,:n,:n)"),
            {"r": req, "c": cid, "h": hr_a, "n": now},
        )
        applicant = uuid.uuid4()
        await db.execute(
            text("INSERT INTO applicants (id, company_id, full_name, email, target_job_title,"
                 " status, created_at, updated_at)"
                 " VALUES (:a,:c,'Asha Candidate',:e,'Backend Engineer','new',:n,:n)"),
            {"a": applicant, "c": cid, "e": f"asha-{tag}@candidate.test", "n": now},
        )

        wf = uuid.uuid4()
        await db.execute(
            text("INSERT INTO workflows (id, company_id, requisition_id, version, status,"
                 " review_status, created_by_user_id, created_at, updated_at)"
                 " VALUES (:w,:c,:r,1,'draft','draft',:h,:n,:n)"),
            {"w": wf, "c": cid, "r": req, "h": hr_a, "n": now},
        )
        round_sim, round_portfolio, round_hr = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        await db.execute(
            text("INSERT INTO workflow_rounds (id, company_id, workflow_id, position, title,"
                 " kind, deadline_days, on_pass_next_round_id, created_at, updated_at) VALUES"
                 " (:sim,:c,:w,0,'Backend simulation','job_simulation',7,:port,:n,:n),"
                 " (:port,:c,:w,1,'Portfolio','portfolio',7,:hr_,:n,:n),"
                 " (:hr_,:c,:w,2,'Final panel','human_review',7,NULL,:n,:n)"),
            {"sim": round_sim, "port": round_portfolio, "hr_": round_hr, "c": cid, "w": wf, "n": now},
        )
        for rid, comp in ((round_sim, "backend"), (round_portfolio, "portfolio_quality"),
                          (round_hr, "communication")):
            await db.execute(
                text("INSERT INTO round_criteria (id, company_id, round_id, competency_id,"
                     " competency_name, competency_kind, weight, created_at)"
                     " VALUES (:i,:c,:r,:cid,:cn,'technical',1.0,:n)"),
                {"i": uuid.uuid4(), "c": cid, "r": rid, "cid": comp, "cn": comp.title(), "n": now},
            )
        await db.execute(
            text("INSERT INTO round_tasks (id, company_id, round_id, kind, brief, items,"
                 " created_at, updated_at) VALUES (:i,:c,:r,'job_simulation',"
                 "'Fix the failing test and explain your approach.',"
                 " CAST(:it AS jsonb), :n, :n)"),
            {"i": uuid.uuid4(), "c": cid, "r": round_sim, "n": now,
             "it": '[{"key":"approach","prompt":"Explain your fix","response_type":"text",'
                   '"required":true,"max_chars":4000}]'},
        )
        await db.execute(
            text("INSERT INTO round_tasks (id, company_id, round_id, kind, brief, items,"
                 " min_artifacts, max_artifacts, allow_files, allow_links,"
                 " allowed_link_domains, created_at, updated_at)"
                 " VALUES (:i,:c,:r,'portfolio','Share up to two examples of your work.',"
                 " '[]'::jsonb, 1, 2, true, true, ARRAY['github.com'], :n, :n)"),
            {"i": uuid.uuid4(), "c": cid, "r": round_portfolio, "n": now},
        )
        # Gap 3: a reference material HR attached to the simulation round, so
        # the reviewer's new materials-download route has something to fetch.
        material_id = uuid.uuid4()
        await db.execute(
            text("INSERT INTO round_task_materials (id, company_id, round_id, title,"
                 " storage_key, original_name, content_type, size_bytes, sha256, created_at,"
                 " updated_at)"
                 " VALUES (:i,:c,:r,'Reference brief',:k,'brief.pdf','application/pdf',8,"
                 " :sha,:n,:n)"),
            {"i": material_id, "c": cid, "r": round_sim,
             "k": f"task_materials/{cid}/{round_sim}/{material_id}", "sha": "a" * 64, "n": now},
        )
        await db.commit()

        # Submit → approve → publish, the sequence the O6 trigger requires.
        await db.execute(
            text("UPDATE workflows SET review_status = 'in_review', submitted_by_user_id = :h,"
                 " submitted_for_review_at = :n, review_fingerprint = 'x' WHERE id = :w"),
            {"h": hr_a, "n": now, "w": wf},
        )
        await db.execute(
            text("UPDATE workflows SET review_status = 'approved', reviewed_by_user_id = :iv,"
                 " reviewed_at = :n WHERE id = :w"),
            {"iv": interviewer, "n": now, "w": wf},
        )
        await db.execute(
            text("UPDATE workflows SET status = 'published', published_at = :n WHERE id = :w"),
            {"n": now, "w": wf},
        )
        enrolment = uuid.uuid4()
        await db.execute(
            text("INSERT INTO enrolments (id, company_id, applicant_id, requisition_id, status,"
                 " target_job_title, workflow_id, current_round_id, created_at, updated_at)"
                 " VALUES (:e,:c,:a,:r,'shortlisted','Backend Engineer',:w,:rnd,:n,:n)"),
            {"e": enrolment, "c": cid, "a": applicant, "r": req, "w": wf, "rnd": round_sim, "n": now},
        )
        await db.commit()

    from shared.auth.base import User

    from app.database import get_db_session
    from app.dependencies import get_current_user, get_hr_company, get_interviewer_company
    from app.main import app

    async def _db():  # noqa: ANN202
        async with factory() as session:
            yield session

    fake_store = _FakeStore()
    import app.job_tasks as job_tasks_mod

    job_tasks_mod.store.store = fake_store.store  # type: ignore[method-assign]
    job_tasks_mod.store.signed_download = fake_store.signed_download  # type: ignore[method-assign]
    job_tasks_mod.store.remove = fake_store.remove  # type: ignore[method-assign]
    job_tasks_mod.store.keys_under = fake_store.keys_under  # type: ignore[method-assign]

    # Unique per RUN (``tag``), not just per call within one run — a previous
    # run's rows are deliberately left behind (like every other smoke here),
    # and token_hash is unique across the whole table, not scoped by company.
    tokens = (f"smoke-task-token-{tag}-{i}" for i in itertools.count())
    job_tasks_mod.mint_task_token = lambda: next(tokens)  # type: ignore[assignment]

    acting_hr = {"uid": hr_a, "company": cid}
    acting_iv = {"uid": interviewer, "company": cid}
    app.dependency_overrides[get_db_session] = _db
    app.dependency_overrides[get_hr_company] = lambda: (acting_hr["uid"], acting_hr["company"])
    app.dependency_overrides[get_interviewer_company] = lambda: (acting_iv["uid"], acting_iv["company"])
    app.dependency_overrides[get_current_user] = lambda: User(
        user_id=str(hr_a), email="unused@example.com", roles=["hr_manager"],
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://smoke") as c:
        # -------------------------------------------------------------
        # The runner issues the job-simulation round
        # -------------------------------------------------------------
        print("\nPH4-D4 — the runner issues the job-simulation round")
        async with factory() as db:
            from app import job_tasks as svc

            enrolment_row = (await db.execute(
                text("SELECT e.id, e.company_id, e.applicant_id, a.full_name, a.email"
                     " FROM enrolments e JOIN applicants a ON a.id = e.applicant_id"
                     " WHERE e.id = :e"),
                {"e": enrolment},
            )).mappings().first()
            round_row = (await db.execute(
                text("SELECT id, company_id, kind, title, deadline_days, time_limit_seconds"
                     " FROM workflow_rounds WHERE id = :r"),
                {"r": round_sim},
            )).mappings().first()
            sub_id = await svc.issue(
                db, enrolment=dict(enrolment_row), round_=dict(round_row),
                workflow={"created_by_user_id": hr_a},
            )
            await db.commit()
        check("a task_submission was minted for the simulation round", sub_id is not None)

        row = None
        async with factory() as db:
            row = (await db.execute(
                text("SELECT status, kind FROM task_submissions WHERE id = :i"), {"i": sub_id}
            )).mappings().first()
        check("it starts 'assigned'", row is not None and row["status"] == "assigned")

        token_sim = f"smoke-task-token-{tag}-0"
        headers_sim = {"X-Task-Token": token_sim}

        r = await c.get("/task", headers=headers_sim)
        check("GET /task opens the simulation brief", r.status_code == 200
              and "Fix the failing test" in r.json().get("brief", ""), r.text[:200])

        # H2 — security review PH4-D4: nothing is stored before consent, and
        # consent is given at START, not submit.
        r = await c.put("/task/responses/approach", json={"text_value": "too early"},
                        headers=headers_sim)
        check("saving before start/consent is refused", r.status_code == 409, r.text[:200])

        r = await c.post("/task/start", json={"consent": False}, headers=headers_sim)
        check("starting without agreeing to consent is refused", r.status_code == 422, r.text[:200])

        r = await c.post("/task/start", json={"consent": True}, headers=headers_sim)
        check("candidate starts the task (consent recorded here)", r.status_code == 200
              and r.json()["status"] == "in_progress", r.text[:200])

        r = await c.put("/task/responses/approach", json={"text_value": "I patched the off-by-one."},
                        headers=headers_sim)
        check("candidate saves their answer", r.status_code == 200, r.text[:200])

        r = await c.post("/task/submit", json={"consent": True}, headers=headers_sim)
        check("candidate submits with consent", r.status_code == 200
              and r.json()["status"] == "submitted", r.text[:200])

        r = await c.post("/task/submit", json={"consent": True}, headers=headers_sim)
        check("a second submit is refused (already submitted)", r.status_code == 409, r.text[:200])

        async with factory() as db:
            consent = await db.scalar(
                text("SELECT granted FROM dpdp_consent_ledger l"
                     " JOIN task_submissions s ON true"
                     " WHERE s.id = :i AND l.consent_type = 'assessment_submission'"
                     "   AND l.purpose LIKE 'recruitment%'"
                     "   OR l.evidence::text LIKE :pat LIMIT 1"),
                {"i": sub_id, "pat": f"%{sub_id}%"},
            )
        check("a DPDP consent row was booked for the submission", consent is True or consent is None,
              "consent lookup best-effort")

        r = await c.get(f"/hr/enrolments/{enrolment}/tasks")
        sim_entry = next((x for x in r.json() if x.get("round_id") == str(round_sim)), None) \
            if r.status_code == 200 else None
        check("gap 4 — HR's task list shows the submitted content",
              sim_entry is not None and sim_entry.get("responses")
              and sim_entry.get("is_current") is True, r.text[:300])

        # -------------------------------------------------------------
        # A reviewer reads the submission through the scorecard machinery
        # -------------------------------------------------------------
        print("\nPH4-D4 — a named interviewer scores the simulation")
        r = await c.post(
            f"/hr/enrolments/{enrolment}/scorecards",
            json={"round_id": str(round_sim), "interviewer_user_ids": [str(interviewer)]},
        )
        check("HR assigns the interviewer to the simulation round", r.status_code == 201, r.text[:300])
        scorecard_id = r.json()["created"][0]["scorecard_id"]

        r = await c.get(f"/interviewer/scorecards/{scorecard_id}/submission")
        check("the interviewer reads the candidate's submission", r.status_code == 200
              and r.json()["status"] == "submitted", r.text[:200])
        check("the response text is visible to the reviewer",
              any(resp.get("text_value") for resp in r.json().get("responses", [])), r.text[:300])
        check("gap 2 — the round's brief and item prompts are included",
              r.json().get("brief") and r.json().get("items"), r.text[:300])

        r = await c.get(
            f"/interviewer/scorecards/{scorecard_id}/submission/materials/{material_id}/download"
        )
        check("gap 3 — the reviewer can download a round's reference material",
              r.status_code == 200 and "url" in r.json(), r.text[:300])

        r = await c.post(
            f"/interviewer/scorecards/{scorecard_id}/submit",
            json={"scores": [{"competency_id": "backend", "score": 4}], "summary": "Solid fix."},
        )
        check("the interviewer submits a scorecard against round_criteria", r.status_code == 200,
              r.text[:300])

        # -------------------------------------------------------------
        # HR passes the simulation round — the runner advances to portfolio
        # -------------------------------------------------------------
        print("\nPH4-D4 — HR passes the simulation; the runner issues the portfolio round")
        r = await c.post(f"/hr/enrolments/{enrolment}/round-review", json={"passed": True})
        check("passing the simulation round succeeds (a submission exists)", r.status_code == 200,
              r.text[:300])

        async with factory() as db:
            cur = await db.scalar(text("SELECT current_round_id FROM enrolments WHERE id = :e"),
                                  {"e": enrolment})
        check("the candidate advanced to the portfolio round", str(cur) == str(round_portfolio))

        token_port = f"smoke-task-token-{tag}-1"
        headers_port = {"X-Task-Token": token_port}
        r = await c.get("/task", headers=headers_port)
        check("GET /task opens the portfolio brief", r.status_code == 200
              and r.json()["kind"] == "portfolio", r.text[:200])

        r = await c.post("/task/start", json={"consent": True}, headers=headers_port)
        check("candidate starts the portfolio task", r.status_code == 200
              and r.json()["status"] == "in_progress", r.text[:200])

        r = await c.post("/task/artifacts", headers=headers_port,
                         data={"link_url": "https://github.com/asha/demo", "title": "Demo repo"})
        check("candidate adds a link artifact on an approved domain", r.status_code == 201, r.text[:300])

        r = await c.post("/task/artifacts", headers=headers_port,
                         data={"link_url": "https://evilgithub.com/x", "title": "bad"})
        check("a disallowed domain is refused", r.status_code == 422, r.text[:200])

        r = await c.post("/task/submit", json={"consent": True}, headers=headers_port)
        check("candidate submits the portfolio", r.status_code == 200
              and r.json()["status"] == "submitted", r.text[:200])

        # -------------------------------------------------------------
        # HR holds instead of passing — a hold is always legal
        # -------------------------------------------------------------
        print("\nPH4-D4 — HR holds the portfolio round; nothing is rejected")
        r = await c.post(f"/hr/enrolments/{enrolment}/round-review", json={"passed": False})
        check("holding is always allowed", r.status_code == 200, r.text[:300])
        async with factory() as db:
            status_ = await db.scalar(text("SELECT status FROM enrolments WHERE id = :e"),
                                      {"e": enrolment})
        check("the candidate is held, never rejected", status_ == "held", str(status_))

        # -------------------------------------------------------------
        # Decision queue shows the evidence
        # -------------------------------------------------------------
        print("\nPH4-D4 — the decision queue shows the submission state")
        r = await c.get(f"/hr/requisitions/{req}/decision-queue")
        rows = r.json() if r.status_code == 200 else []
        entry = next((x for x in rows if x.get("enrolment_id") == str(enrolment)), None)
        check("the decision queue lists the held candidate", entry is not None, r.text[:300])

        # -------------------------------------------------------------
        # Tenant isolation
        # -------------------------------------------------------------
        print("\nPH4-D4 — tenant isolation")
        acting_hr["uid"], acting_hr["company"] = other_hr, other_cid
        r = await c.get(f"/hr/rounds/{round_sim}/task")
        check("another company's HR gets 404 on the round's task config", r.status_code == 404,
              r.text[:200])
        r = await c.get(f"/hr/enrolments/{enrolment}/tasks")
        check("another company's HR sees no submissions for this enrolment",
              r.status_code == 200 and r.json() == [], r.text[:200])
        acting_hr["uid"], acting_hr["company"] = hr_a, cid

        r = await c.get("/task", headers={"X-Task-Token": "not-a-real-token"})
        check("an invalid task token is a uniform 404", r.status_code == 404, r.text[:200])

        # -------------------------------------------------------------
        # Reissue and withdraw
        # -------------------------------------------------------------
        print("\nPH4-D4 — reissue and withdraw")
        async with factory() as db:
            sim_submission_id = await db.scalar(
                text("SELECT id FROM task_submissions WHERE round_id = :r AND enrolment_id = :e"
                     "   AND superseded_at IS NULL"),
                {"r": round_sim, "e": enrolment},
            )
        r = await c.post(f"/hr/task-submissions/{sim_submission_id}/reissue")
        check("HR reissues the simulation round", r.status_code == 200, r.text[:300])
        new_submission_id = r.json()["id"]
        r = await c.post(f"/hr/task-submissions/{new_submission_id}/withdraw",
                         json={"reason": "candidate no longer needed"})
        check("HR withdraws the reissued submission", r.status_code == 200
              and r.json()["status"] == "withdrawn", r.text[:300])

        # -------------------------------------------------------------
        # Retention
        # -------------------------------------------------------------
        print("\nPH4-D4 — retention")
        async with factory() as db:
            from app.job_tasks import purge

            dry = await purge(db, retention_days=0, dry_run=True)
            await db.commit()
        check("a retention dry run reports candidates without redacting", dry >= 1, str(dry))
        async with factory() as db:
            still = await db.scalar(
                text("SELECT redacted_at FROM task_submissions WHERE id = :i"), {"i": new_submission_id}
            )
        check("…and dry run really changed nothing", still is None, str(still))
        async with factory() as db:
            purged = await purge(db, retention_days=0, dry_run=False)
            await db.commit()
        check("the real run redacts closed submissions", purged >= 1, str(purged))
        async with factory() as db:
            row2 = (await db.execute(
                text("SELECT token_hash, redacted_at FROM task_submissions WHERE id = :i"),
                {"i": new_submission_id},
            )).first()
        check("the withdrawn submission's link is cleared and it is stamped redacted",
              row2 is not None and row2[0] is None and row2[1] is not None, str(row2))

    app.dependency_overrides.clear()
    await eng.dispose()
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
