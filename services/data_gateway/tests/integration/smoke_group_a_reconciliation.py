"""A1/A6 smoke test — reconciliation loop and scheduled-job bookkeeping.

Standalone smoke script, not a pytest module — it needs a live PostgreSQL with
pgvector and drives the real code paths end to end, so it is deliberately kept
out of the unit run.

    docker run -d --name intants-pgv -e POSTGRES_PASSWORD=postgres         -e POSTGRES_DB=intants_smoke -p 55432:5432 pgvector/pgvector:pg16
    cd services/data_gateway
    DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:55432/intants_smoke         python -m alembic upgrade head
    PYTHONPATH=".;../.." python tests/integration/smoke_group_a_reconciliation.py

Exits non-zero on any failed check, so it can gate a release.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.reconciliation as rec
from app.scheduling import job_status, run_scheduled_job

URL = "postgresql+asyncpg://postgres:postgres@127.0.0.1:55432/intants_smoke"

PASS, FAIL = [], []


def check(label: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(label)
    print(f"  {'PASS' if cond else 'FAIL'}  {label}{(' — ' + detail) if detail and not cond else ''}")


async def seed(f: async_sessionmaker) -> dict[str, uuid.UUID]:
    cid, uid = uuid.uuid4(), uuid.uuid4()
    ids = {k: uuid.uuid4() for k in ("scoreme", "willfail", "done", "noresume")}
    now = datetime.now(tz=UTC)
    async with f() as db:
        await db.execute(text(
            "INSERT INTO companies (id, name, slug, is_active, created_at, updated_at) "
            "VALUES (:i,'Acme','acme',true,:n,:n)"), {"i": cid, "n": now})
        await db.execute(text(
            "INSERT INTO users (id, email, full_name, password_hash, company_id, preferred_language, "
            " is_active, notify_login_email, must_change_password, created_at, updated_at) "
            "VALUES (:i,'hr@acme.test','HR','x',:c,'en',true,false,false,:n,:n)"), {"i": uid, "c": cid, "n": now})
        rows = [
            (ids["scoreme"],  "Asha",  "cv text", None),
            (ids["willfail"], "Bharat", "cv text", None),
            (ids["done"],     "Chitra", "cv text", 8),
            (ids["noresume"], "Dev",    None,      None),
        ]
        for i, (aid, name, cv, score) in enumerate(rows):
            await db.execute(text(
                "INSERT INTO applicants (id, company_id, created_by_user_id, full_name, email, "
                " target_job_title, target_level, resume_text, ats_overall, status, created_at, updated_at) "
                "VALUES (:i,:c,:u,:fn,:em,'Python Developer','mid',:cv,:sc,'new',:n,:n)"),
                {"i": aid, "c": cid, "u": uid, "fn": name, "em": f"{name.lower()}@x.test",
                 "cv": cv, "sc": score, "n": now - timedelta(minutes=10 - i)})
        await db.commit()
    return {**ids, "company": cid, "user": uid}


async def main() -> None:
    eng = create_async_engine(URL)
    f = async_sessionmaker(eng, expire_on_commit=False)

    async with eng.begin() as c:
        for t in ("reconciliation_state", "scheduled_job_runs"):
            await c.execute(text(f"TRUNCATE {t}"))
        await c.execute(text(
            "TRUNCATE applicants, companies, users, email_events, notifications CASCADE"))

    ids = await seed(f)
    print("\n--- seeded: 1 scorable, 1 that will fail, 1 already scored, 1 with no CV ---\n")

    # ── Pass 1: one row scores, one fails ────────────────────────────────
    async def scorer(**kw: object) -> dict[str, object]:
        if kw.get("resume_text") and "Bharat" in str(kw.get("job_title", "")) :
            raise RuntimeError("unreachable")
        raise RuntimeError("scorer down")  # replaced below

    calls: list[str] = []

    async def scorer_mixed(**kw: object) -> dict[str, object]:
        calls.append("call")
        # Fail the first row we are handed, succeed for the rest.
        if len(calls) == 1:
            raise RuntimeError("scorer 503")
        return {"overall": 7, "breakdown": {"skills": 7}, "strengths": ["a"],
                "concerns": ["b"], "recommendation": "interview", "summary": "ok"}

    embed_calls: list[str] = []

    async def embedder_down(**_: object) -> list[list[float]]:
        embed_calls.append("call")
        raise RuntimeError("embedder 503")

    rec.score_resume_remote = scorer_mixed  # type: ignore[assignment]
    rec.embed_texts_remote = embedder_down  # type: ignore[assignment]
    r1 = await rec.run_once(f)
    print(f"  pass 1 -> {r1.as_dict()}\n")

    check("one applicant scored", r1.scored == 1, f"scored={r1.scored}")
    check("ats failure + embed failures recorded", r1.failed == 4, f"failed={r1.failed}")
    check("embedder was called once per chunk", len(embed_calls) == 1, f"chunks={len(embed_calls)}")
    check("already-scored row untouched", r1.outstanding["unscored"] == 1,
          f"unscored={r1.outstanding['unscored']}")

    async with f() as db:
        st = (await db.execute(text(
            "SELECT kind, attempts, next_attempt_at, gave_up_at, last_error "
            "FROM reconciliation_state WHERE kind = 'applicant_ats'"))).mappings().all()
        check("one ats backoff row written", len(st) == 1, f"rows={len(st)}")
        if st:
            row = st[0]
            check("attempts = 1", row["attempts"] == 1, str(row["attempts"]))
            check("retry scheduled in the future", row["next_attempt_at"] > datetime.now(tz=UTC))
            check("not given up yet", row["gave_up_at"] is None)
            check("error text captured", "503" in (row["last_error"] or ""))

        scored_ok = await db.scalar(text(
            "SELECT count(*) FROM applicants WHERE ats_overall = 7 AND ats_recommendation='interview'"))
        check("ATS fields written through the shared mapper", scored_ok == 1, f"count={scored_ok}")

        no_cv = await db.scalar(text(
            "SELECT ats_overall FROM applicants WHERE id = :i"), {"i": ids["noresume"]})
        check("applicant with no CV was never attempted", no_cv is None)

    # ── Pass 2: the backing-off row must be skipped ──────────────────────
    calls.clear()
    r2 = await rec.run_once(f)
    print(f"\n  pass 2 -> {r2.as_dict()}")
    check("backing-off row skipped (no scorer call)", len(calls) == 0, f"calls={len(calls)}")
    check("pass 2 did nothing", r2.scored == 0 and r2.failed == 0)

    # ── Pass 3: due again, and at the give-up threshold ──────────────────
    async with f() as db:
        await db.execute(text(
            "UPDATE reconciliation_state SET next_attempt_at = now() - interval '1 minute', "
            "attempts = :a WHERE kind = 'applicant_ats'"), {"a": rec.MAX_ATTEMPTS - 1})
        await db.commit()

    async def always_fail(**_: object) -> dict[str, object]:
        calls.append("call")
        raise RuntimeError("still down")

    rec.score_resume_remote = always_fail  # type: ignore[assignment]
    r3 = await rec.run_once(f)
    print(f"  pass 3 -> {r3.as_dict()}")
    check("ats row retried once due", len(calls) == 1, f"calls={len(calls)}")
    check("ats row gave up at the limit", r3.gave_up == 1, f"gave_up={r3.gave_up}")

    async with f() as db:
        gu = await db.scalar(text("SELECT gave_up_at FROM reconciliation_state WHERE kind = 'applicant_ats'"))
        check("gave_up_at stamped", gu is not None)
        parked = (await rec._outstanding(db))["parked"]
        check("parked row surfaces in outstanding", parked >= 1, f"parked={parked}")
        emb = (await db.execute(text("SELECT attempts FROM reconciliation_state WHERE kind = 'applicant_embedding'"))).scalars().all()
        check("embed failures also backed off per row", len(emb) == 3 and all(a >= 1 for a in emb), str(emb))

    # ── Pass 4: a parked row must never be selected again ────────────────
    calls.clear()
    r4 = await rec.run_once(f)
    print(f"  pass 4 -> {r4.as_dict()}")
    check("parked ats row is excluded by the partial index predicate", len(calls) == 0,
          f"calls={len(calls)}")

    # ── Embedding write — reachable now that pgvector is present ─────────
    print("")
    print("--- embedding write (halfvec, previously unverifiable) ---")
    async with f() as db:
        await db.execute(text("DELETE FROM reconciliation_state WHERE kind = 'applicant_embedding'"))
        await db.commit()

    dim_n = 3072

    async def embedder_ok(*, texts, **_: object) -> list[list[float]]:
        return [[0.01 * ((i % 7) + 1)] * dim_n for i, _t in enumerate(texts)]

    rec.embed_texts_remote = embedder_ok  # type: ignore[assignment]
    r5 = await rec.run_once(f)
    print(f"  embed pass -> {r5.as_dict()}")
    check("embeddings written for every applicant with a CV", r5.embedded == 3,
          f"embedded={r5.embedded}")

    async with f() as db:
        n = await db.scalar(text("SELECT count(*) FROM applicants WHERE embedding IS NOT NULL"))
        check("halfvec column populated", n == 3, f"count={n}")
        dim = await db.scalar(text(
            "SELECT vector_dims(embedding::vector) FROM applicants WHERE embedding IS NOT NULL LIMIT 1"))
        check(f"stored vector has {dim_n} dimensions", dim == dim_n, f"dims={dim}")
        # The HNSW index must actually be usable for the search this feeds.
        probe = "[" + ",".join(["0.02"] * dim_n) + "]"
        hits = (await db.execute(text(
            "SELECT full_name FROM applicants WHERE embedding IS NOT NULL "
            "ORDER BY embedding <=> CAST(:q AS halfvec) LIMIT 3"), {"q": probe})).scalars().all()
        check("cosine search over the written vectors returns rows", len(hits) == 3, str(hits))
        left = (await rec._outstanding(db))["unembedded"]
        check("outstanding embedding work drains to zero", left == 0, f"unembedded={left}")

    r6 = await rec.run_once(f)
    check("nothing left to do on the next pass", r6.embedded == 0 and r6.scored == 0,
          str(r6.as_dict()))

    # ── A6: the claim SQL, including the OR-precedence branch ────────────
    print("\n--- A6: scheduled job claim ---")
    ran: list[int] = []

    async def job() -> None:
        ran.append(1)

    did1 = await run_scheduled_job(f, job_id="smoke", interval=timedelta(days=1), fn=job)
    did2 = await run_scheduled_job(f, job_id="smoke", interval=timedelta(days=1), fn=job)
    check("first call runs the job", did1 is True and len(ran) == 1)
    check("second call is refused as not due", did2 is False and len(ran) == 1)

    async with f() as db:
        await db.execute(text(
            "UPDATE scheduled_job_runs SET last_finished_at = now() - interval '2 days'"))
        await db.commit()
    did3 = await run_scheduled_job(f, job_id="smoke", interval=timedelta(days=1), fn=job)
    check("overdue job runs again", did3 is True and len(ran) == 2)

    async with f() as db:
        await db.execute(text(
            "UPDATE scheduled_job_runs SET last_status='running', "
            "last_started_at = now() - interval '9 hours', last_finished_at = NULL"))
        await db.commit()
    did4 = await run_scheduled_job(f, job_id="smoke", interval=timedelta(days=1), fn=job)
    check("stale 'running' marker is reclaimed", did4 is True and len(ran) == 3)

    async def boom() -> None:
        raise RuntimeError("job exploded")

    async with f() as db:
        await db.execute(text(
            "UPDATE scheduled_job_runs SET last_finished_at = now() - interval '2 days', "
            "last_status='ok'"))
        await db.commit()
    did5 = await run_scheduled_job(f, job_id="smoke", interval=timedelta(days=1), fn=boom)
    check("a raising job does not propagate", did5 is True)
    status = await job_status(f)
    check("failure recorded as status=error", status[0]["last_status"] == "error",
          str(status[0]["last_status"]))
    check("error message retained", "exploded" in str(status[0]["last_error"]))
    check("run_count incremented", status[0]["run_count"] == 4, str(status[0]["run_count"]))

    await eng.dispose()

    print(f"\n{'='*64}\n  {len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("  FAILED:", ", ".join(FAIL))
    print("=" * 64)
    raise SystemExit(1 if FAIL else 0)


asyncio.run(main())
