"""A6 smoke test — catching up on missed windows, retrying failures, history.

Standalone script, not a pytest module: the claim is a single SQL statement and
its behaviour only means anything against a real PostgreSQL. Needs a throwaway
database at head (it clears the two scheduler tables).

    cd services/data_gateway
    DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:55432/intants_smoke \
      python -m alembic upgrade head
    PYTHONPATH=".;../.." python tests/integration/smoke_group_a_scheduler.py
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.scheduling as sch

URL = "postgresql+asyncpg://postgres:postgres@127.0.0.1:55432/intants_smoke"
PASS, FAIL = [], []
DAY = timedelta(days=1)


def check(label: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(label)
    print(f"  {'PASS' if cond else 'FAIL'}  {label}{(' — ' + detail) if detail and not cond else ''}")


async def main() -> None:
    eng = create_async_engine(URL)
    f = async_sessionmaker(eng, expire_on_commit=False)
    async with eng.begin() as c:
        await c.execute(text("TRUNCATE scheduled_job_runs, scheduled_job_run_log"))

    ran: list[str] = []

    def job(name: str, fail: bool = False):  # noqa: ANN202
        async def _fn() -> None:
            ran.append(name)
            if fail:
                raise RuntimeError("Key (email)=(asha.rao@example.com) already exists")
        return _fn

    async def set_last(job_id: str, ago: timedelta, status: str) -> None:
        at = datetime.now(tz=UTC) - ago
        async with f() as db:
            await db.execute(text(
                "INSERT INTO scheduled_job_runs (job_id, last_started_at, last_finished_at,"
                " last_status, run_count) VALUES (:j, :t, :t, :s, 1)"
                " ON CONFLICT (job_id) DO UPDATE SET last_started_at = :t,"
                " last_finished_at = :t, last_status = :s"),
                {"j": job_id, "t": at, "s": status})
            await db.commit()

    # A nightly job that ran 23h40m ago: the scheduler's own hour is close, so
    # the scheduler may run it; catch-up must not get in first.
    await set_last("nearly_due", timedelta(hours=23, minutes=40), "ok")
    await sch.run_overdue_jobs_on_startup(f, [("nearly_due", DAY, job("nearly_due"))])
    check("catch-up leaves a window the scheduler is about to run", "nearly_due" not in ran)
    ok = await sch.run_scheduled_job(f, job_id="nearly_due", interval=DAY, fn=job("nearly_due"))
    check("the scheduler's own trigger still runs it", ok and "nearly_due" in ran)

    # Missed by hours — the Space slept through it.
    await set_last("missed", timedelta(hours=30), "ok")
    await sch.run_overdue_jobs_on_startup(f, [("missed", DAY, job("missed"))])
    check("catch-up runs a window that was really missed", ran.count("missed") == 1)
    await sch.run_overdue_jobs_on_startup(f, [("missed", DAY, job("missed"))])
    check("and does not run it again on the next tick", ran.count("missed") == 1)

    # A failure — then the retry an hour later, not a day later.
    await set_last("flaky", timedelta(hours=30), "ok")
    await sch.run_overdue_jobs_on_startup(f, [("flaky", DAY, job("flaky", fail=True))])
    await sch.run_overdue_jobs_on_startup(f, [("flaky", DAY, job("flaky"))])
    check("a failed run is not retried straight away", ran.count("flaky") == 1)
    async with f() as db:
        await db.execute(text("UPDATE scheduled_job_runs SET last_finished_at = now() - interval"
                              " '61 minutes' WHERE job_id = 'flaky'"))
        await db.commit()
    await sch.run_overdue_jobs_on_startup(f, [("flaky", DAY, job("flaky"))])
    check("a failed run is retried after an hour, not the next night", ran.count("flaky") == 2)

    # Two instances waking together.
    await set_last("contended", timedelta(hours=30), "ok")
    results = await asyncio.gather(
        sch.run_scheduled_job(f, job_id="contended", interval=DAY, fn=job("contended"),
                              trigger="catchup"),
        sch.run_scheduled_job(f, job_id="contended", interval=DAY, fn=job("contended"),
                              trigger="catchup"),
    )
    check("two instances waking at once run the job once", ran.count("contended") == 1
          and sorted(results) == [False, True], str(results))

    # Interval loops.
    await sch.record_loop_pass(f, "reminders_sweep", started_at=datetime.now(tz=UTC))
    await sch.record_loop_pass(f, "reminders_sweep", started_at=datetime.now(tz=UTC),
                               error="expiry: ProgrammingError: call +91 98765 43210")

    status = {j["job_id"]: j for j in await sch.job_status(f)}
    flaky = status["flaky"]["recent"]
    check("history keeps every run, newest first",
          [r["status"] for r in flaky] == ["ok", "error"], str(flaky))
    check("history says what triggered each run",
          {r["trigger"] for r in flaky} == {"catchup"}, str(flaky))
    check("an email in an error is masked before it is stored",
          all("asha" not in (r["error"] or "") for r in flaky)
          and any("<email>" in (r["error"] or "") for r in flaky), str(flaky))
    loop = status["reminders_sweep"]
    check("a loop's summary row counts every pass", loop["run_count"] == 2, str(loop))
    check("a loop's history holds only its failure",
          [r["status"] for r in loop["recent"]] == ["error"]
          and "<number>" in (loop["recent"][0]["error"] or ""), str(loop["recent"]))

    await eng.dispose()
    print(f"\n{'='*64}\n  {len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("  FAILED:", ", ".join(FAIL))
    print("=" * 64)
    raise SystemExit(1 if FAIL else 0)


asyncio.run(main())
