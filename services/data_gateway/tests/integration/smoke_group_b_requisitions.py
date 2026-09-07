"""Group B smoke test — requisitions, enrolments, the ledger and applicant merge.

Standalone script, not a pytest module: it needs a live PostgreSQL with pgvector
and drives the real code paths end to end.

    docker run -d --name intants-pgv -e POSTGRES_PASSWORD=postgres \
        -e POSTGRES_DB=intants_smoke -p 55432:5432 pgvector/pgvector:pg16
    cd services/data_gateway
    DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:55432/intants_smoke \
        python -m alembic upgrade head
    PYTHONPATH=".;../.." python tests/integration/smoke_group_b_requisitions.py

Assumes the pre-Group-B seed has been applied and the migration has run, so the
backfill's output is what is under test. Exits non-zero on any failed check.
"""
from __future__ import annotations

import asyncio
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.requisitions import (
    merge_applicants,
    merge_candidates,
    normalise_title,
    record_transition,
    time_in_stage_days,
)

URL = "postgresql+asyncpg://postgres:postgres@127.0.0.1:55432/intants_smoke"
PASS, FAIL = [], []


def check(label: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(label)
    print(f"  {'PASS' if cond else 'FAIL'}  {label}{(' — ' + detail) if detail and not cond else ''}")


async def main() -> None:
    eng = create_async_engine(URL)
    f = async_sessionmaker(eng, expire_on_commit=False)

    async with f() as db:
        acme = await db.scalar(text("SELECT id FROM companies WHERE slug='acme'"))
        globex = await db.scalar(text("SELECT id FROM companies WHERE slug='globex'"))
        hr = await db.scalar(text("SELECT id FROM users WHERE email='hr@acme.test'"))

    # ── Backfill shape ───────────────────────────────────────────────────
    print("\n--- backfill ---")
    async with f() as db:
        reqs = (await db.execute(text(
            "SELECT title, count(e.id) n FROM job_requisitions r"
            " LEFT JOIN enrolments e ON e.requisition_id=r.id"
            " WHERE r.company_id=:c GROUP BY r.title ORDER BY r.title"),
            {"c": acme})).all()
        by_title = dict(reqs)
        check("four spellings collapsed into one Python opening",
              by_title.get("Python Developer") == 5, str(by_title))
        check("Staff Nurse is a separate opening", by_title.get("Staff Nurse") == 3, str(by_title))
        cross = await db.scalar(text(
            "SELECT count(*) FROM job_requisitions WHERE company_id=:g"), {"g": globex})
        check("the other company keeps its own Python opening", cross == 1, f"count={cross}")
        orphan = await db.scalar(text(
            "SELECT count(*) FROM enrolments e JOIN applicants a ON a.id=e.applicant_id"
            " WHERE a.deleted_at IS NOT NULL"))
        check("soft-deleted applicants were not enrolled", orphan == 0, f"count={orphan}")
        linked = await db.scalar(text(
            "SELECT count(*) FROM exam_assignments WHERE enrolment_id IS NULL"))
        check("every exam assignment points at an enrolment", linked == 0, f"unlinked={linked}")

    # ── Transitions ──────────────────────────────────────────────────────
    print("\n--- transitions ---")
    async with f() as db:
        enr = (await db.execute(text(
            "SELECT e.id, e.status, e.applicant_id FROM enrolments e"
            " JOIN applicants a ON a.id=e.applicant_id"
            " WHERE a.full_name='Bharat'"))).first()
        eid, before, aid = enr[0], enr[1], enr[2]

        prev = await record_transition(
            db, enrolment_id=eid, company_id=acme, to_status="shortlisted",
            actor_user_id=hr, automated=False, reason="strong CV")
        await db.commit()
        check("transition returns the previous status", prev == before, f"{prev} vs {before}")

        led = (await db.execute(text(
            "SELECT from_status, to_status, automated, actor_user_id, reason"
            " FROM stage_transitions WHERE enrolment_id=:e ORDER BY occurred_at DESC LIMIT 1"),
            {"e": eid})).mappings().first()
        check("ledger entry written", led is not None)
        check("ledger records the human actor",
              led["actor_user_id"] == hr and led["automated"] is False, str(dict(led)))
        check("ledger records the reason", led["reason"] == "strong CV")

        synced = await db.scalar(text("SELECT status FROM applicants WHERE id=:a"), {"a": aid})
        check("legacy applicants.status kept in step (single enrolment)",
              synced == "shortlisted", f"got {synced}")

        # A no-op move must not manufacture history.
        n_before = await db.scalar(text(
            "SELECT count(*) FROM stage_transitions WHERE enrolment_id=:e"), {"e": eid})
        again = await record_transition(
            db, enrolment_id=eid, company_id=acme, to_status="shortlisted",
            actor_user_id=hr, automated=False)
        await db.commit()
        n_after = await db.scalar(text(
            "SELECT count(*) FROM stage_transitions WHERE enrolment_id=:e"), {"e": eid})
        check("re-saving the same status writes nothing",
              again is None and n_before == n_after, f"{n_before} -> {n_after}")

        days = await time_in_stage_days(db, eid)
        check("time-in-stage derives from the ledger", days is not None and days < 1, str(days))

        # Cross-tenant move must be refused.
        cross = await record_transition(
            db, enrolment_id=eid, company_id=globex, to_status="hired",
            actor_user_id=hr, automated=False)
        await db.rollback()
        check("a move scoped to the wrong company is refused", cross is None)

    # ── The dual-enrolment case D-06a exists for ─────────────────────────
    print("\n--- one person, two openings ---")
    async with f() as db:
        gita = (await db.execute(text(
            "SELECT e.id, r.title FROM enrolments e"
            " JOIN applicants a ON a.id=e.applicant_id"
            " JOIN job_requisitions r ON r.id=e.requisition_id"
            " WHERE a.full_name='Gita' ORDER BY r.title"))).all()
        check("Gita holds two enrolments across two openings", len(gita) == 2, str(gita))

        gita_app = await db.scalar(text(
            "SELECT id FROM applicants WHERE full_name='Gita' AND deleted_at IS NULL LIMIT 1"))
        legacy_before = await db.scalar(text(
            "SELECT status FROM applicants WHERE id=:a"), {"a": gita_app})
        # Both rows are still separate applicants here, so each has one
        # enrolment and the legacy column does still sync. The real test of the
        # guard comes after the merge, below.
        await record_transition(
            db, enrolment_id=gita[0][0], company_id=acme, to_status="hired",
            actor_user_id=hr, automated=False)
        await db.commit()
        check("independent enrolments move independently",
              (await db.scalar(text("SELECT status FROM enrolments WHERE id=:e"),
                               {"e": gita[1][0]})) == "new",
              "the second enrolment should be untouched")
        _ = legacy_before

    # ── Merge ────────────────────────────────────────────────────────────
    print("\n--- merge candidates ---")
    async with f() as db:
        cands = await merge_candidates(db, acme)
        check("duplicate email detected as one person", len(cands) == 1, str(cands))
        if cands:
            c = cands[0]
            check("candidate names two applicant rows", len(c.applicant_ids) == 2, str(c.as_dict()))
            check("candidate reports its enrolment count", c.enrolment_count == 2,
                  str(c.enrolment_count))

        # Merging must refuse when both are in the same opening.
        acme_python = await db.scalar(text(
            "SELECT id FROM job_requisitions WHERE company_id=:c AND title='Python Developer'"),
            {"c": acme})
        asha, bharat = (await db.execute(text(
            "SELECT id FROM applicants WHERE full_name IN ('Asha','Bharat')"
            " AND deleted_at IS NULL ORDER BY full_name"))).scalars().all()
        try:
            await merge_applicants(db, company_id=acme, survivor_id=asha,
                                   absorbed_ids=[bharat], actor_user_id=hr)
            await db.rollback()
            check("merge refused when both share an opening", False, "it succeeded")
        except ValueError as exc:
            await db.rollback()
            check("merge refused when both share an opening",
                  "same opening" in str(exc), str(exc))
        _ = acme_python

        # Cross-company merge must refuse.
        hari = await db.scalar(text(
            "SELECT id FROM applicants WHERE full_name='Hari'"))
        try:
            await merge_applicants(db, company_id=acme, survivor_id=asha,
                                   absorbed_ids=[hari], actor_user_id=hr)
            await db.rollback()
            check("cross-company merge refused", False, "it succeeded")
        except ValueError as exc:
            await db.rollback()
            check("cross-company merge refused", "belong to this company" in str(exc), str(exc))

    print("\n--- merge execution ---")
    async with f() as db:
        gita_ids = (await db.execute(text(
            "SELECT id FROM applicants WHERE full_name='Gita' AND deleted_at IS NULL"
            " ORDER BY created_at"))).scalars().all()
        survivor, absorbed = gita_ids[0], gita_ids[1]
        moved = await merge_applicants(db, company_id=acme, survivor_id=survivor,
                                       absorbed_ids=[absorbed], actor_user_id=hr)
        await db.commit()
        check("merge moved the enrolment", moved["enrolments"] == 1, str(moved))

        n = await db.scalar(text(
            "SELECT count(*) FROM enrolments WHERE applicant_id=:s AND deleted_at IS NULL"),
            {"s": survivor})
        check("survivor now holds both enrolments", n == 2, f"count={n}")
        gone = await db.scalar(text(
            "SELECT deleted_at IS NOT NULL FROM applicants WHERE id=:a"), {"a": absorbed})
        check("absorbed applicant soft-deleted", gone is True)
        pii = await db.scalar(text(
            "SELECT full_name FROM applicants WHERE id=:a"), {"a": absorbed})
        check("absorbed row keeps its data (a merge is not an erasure)", pii == "Gita", str(pii))
        left = await merge_candidates(db, acme)
        check("no merge candidates remain", left == [], str(left))

        # NOW the survivor has two enrolments, so the legacy column must not be
        # guessed at — this is the guard D-06a exists for.
        both = (await db.execute(text(
            "SELECT id, status FROM enrolments WHERE applicant_id=:s ORDER BY status"),
            {"s": survivor})).all()
        legacy_before = await db.scalar(text(
            "SELECT status FROM applicants WHERE id=:a"), {"a": survivor})
        target = next(e for e in both if e[1] != "rejected")
        await record_transition(
            db, enrolment_id=target[0], company_id=acme, to_status="rejected",
            actor_user_id=hr, automated=False)
        await db.commit()
        legacy_after = await db.scalar(text(
            "SELECT status FROM applicants WHERE id=:a"), {"a": survivor})
        check("legacy status NOT overwritten when a person holds several enrolments",
              legacy_before == legacy_after, f"{legacy_before} -> {legacy_after}")

    # ── Normalisation parity is load-bearing ─────────────────────────────
    print("\n--- title normalisation ---")
    async with f() as db:
        for t in ["Python Developer", "  PYTHON   developer ", "Staff\tNurse", "Ops"]:
            sql = await db.scalar(text(
                "SELECT lower(btrim(regexp_replace(:t, '\\s+', ' ', 'g')))"), {"t": t})
            check(f"python matches SQL for {t!r}", sql == normalise_title(t),
                  f"{sql!r} vs {normalise_title(t)!r}")

        dup = uuid.uuid4()
        try:
            await db.execute(text(
                "INSERT INTO job_requisitions (id,company_id,title,created_at,updated_at)"
                " VALUES (:i,:c,'  python   DEVELOPER  ',now(),now())"), {"i": dup, "c": acme})
            await db.commit()
            check("database refuses a duplicate normalised title", False, "insert succeeded")
        except Exception as exc:  # noqa: BLE001
            await db.rollback()
            check("database refuses a duplicate normalised title",
                  "uq_job_requisitions_company_title" in str(exc), type(exc).__name__)

    await eng.dispose()
    print(f"\n{'=' * 64}\n  {len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("  FAILED:", ", ".join(FAIL))
    print("=" * 64)
    raise SystemExit(1 if FAIL else 0)


asyncio.run(main())
