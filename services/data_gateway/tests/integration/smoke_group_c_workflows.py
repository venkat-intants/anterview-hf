"""Group C smoke test — workflow authoring, versioning, frozen criteria.

Standalone script; needs a live PostgreSQL with pgvector.

    docker run -d --name intants-pgv -e POSTGRES_PASSWORD=postgres \
        -e POSTGRES_DB=intants_smoke -p 55432:5432 pgvector/pgvector:pg16
    cd services/data_gateway
    export DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:55432/intants_smoke
    python -m alembic upgrade head
    PYTHONPATH=".;../.." python tests/integration/smoke_group_c_workflows.py

The properties under test are the ones that would be expensive to get wrong:
a published workflow cannot be edited, cloning preserves the chain exactly, and
criteria are a frozen copy rather than a live reference.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.workflows import (
    WorkflowError,
    add_round,
    clone_for_edit,
    create_draft,
    load_criteria,
    load_rounds,
    publish,
    published_workflow,
    set_round_criteria,
    validate,
    validate_chain,
)

URL = "postgresql+asyncpg://postgres:postgres@127.0.0.1:55432/intants_smoke"
PASS, FAIL = [], []

# A stand-in for what shared/intelligence derives for a Python developer.
PROFILE = [
    {"id": "python_proficiency", "name": "Python proficiency", "kind": "technical", "weight": 0.30},
    {"id": "problem_solving", "name": "Problem solving", "kind": "cognitive", "weight": 0.25},
    {"id": "code_quality", "name": "Code quality", "kind": "technical", "weight": 0.20},
    {"id": "communication", "name": "Communication", "kind": "behavioural", "weight": 0.15},
    {"id": "version_control", "name": "Version control", "kind": "technical", "weight": 0.10},
]


def check(label: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(label)
    print(f"  {'PASS' if cond else 'FAIL'}  {label}{(' — ' + detail) if detail and not cond else ''}")


async def _fixture(db) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    now = datetime.now(tz=UTC)
    cid, uid, rid = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    await db.execute(text(
        "INSERT INTO companies (id,name,slug,is_active,created_at,updated_at)"
        " VALUES (:i,'Acme','acme',true,:t,:t)"), {"i": cid, "t": now})
    await db.execute(text(
        "INSERT INTO users (id,email,full_name,password_hash,company_id,preferred_language,"
        " is_active,notify_login_email,must_change_password,created_at,updated_at)"
        " VALUES (:i,'hr@acme.test','HR','x',:c,'en',true,false,false,:t,:t)"),
        {"i": uid, "c": cid, "t": now})
    await db.execute(text(
        "INSERT INTO job_requisitions (id,company_id,title,created_at,updated_at)"
        " VALUES (:i,:c,'Python Developer',:t,:t)"), {"i": rid, "c": cid, "t": now})
    await db.commit()
    return cid, uid, rid


async def main() -> None:
    eng = create_async_engine(URL)
    f = async_sessionmaker(eng, expire_on_commit=False)
    async with eng.begin() as c:
        await c.execute(text(
            "TRUNCATE companies, users, applicants, job_requisitions, enrolments,"
            " stage_transitions, workflows, workflow_rounds, round_criteria,"
            " round_results CASCADE"))
    async with f() as db:
        cid, uid, rid = await _fixture(db)

    # ── Build a draft ────────────────────────────────────────────────────
    print("\n--- authoring ---")
    async with f() as db:
        wf = await create_draft(db, company_id=cid, requisition_id=rid,
                                created_by=uid, name="Standard technical")
        await db.commit()
        check("draft created at version 1",
              (await db.scalar(text("SELECT version FROM workflows WHERE id=:i"), {"i": wf})) == 1)

        try:
            await create_draft(db, company_id=cid, requisition_id=rid, created_by=uid)
            await db.rollback()
            check("a second draft is refused", False, "it was allowed")
        except WorkflowError as exc:
            await db.rollback()
            check("a second draft is refused", "already has a draft" in str(exc), str(exc))

        exam_ref = uuid.uuid4()
        r1 = await add_round(db, company_id=cid, workflow_id=wf, title="Aptitude",
                             kind="mcq", pass_threshold=60, exam_round_id=exam_ref,
                             criteria=[PROFILE[1]])
        r2 = await add_round(db, company_id=cid, workflow_id=wf, title="Technical",
                             kind="coding", pass_threshold=50, exam_round_id=exam_ref,
                             criteria=[PROFILE[0], PROFILE[1], PROFILE[2]])
        r3 = await add_round(db, company_id=cid, workflow_id=wf, title="AI Interview",
                             kind="ai_interview", pass_threshold=60,
                             criteria=[PROFILE[0], PROFILE[1], PROFILE[3]])
        r4 = await add_round(db, company_id=cid, workflow_id=wf, title="HR Final",
                             kind="human_review")
        await db.commit()

        rounds = await load_rounds(db, wf)
        check("four rounds in order", [r["title"] for r in rounds] ==
              ["Aptitude", "Technical", "AI Interview", "HR Final"], str(rounds))
        chain = {str(r["id"]): r["on_pass_next_round_id"] for r in rounds}
        check("appending wired the chain automatically",
              str(chain[str(r1)]) == str(r2) and str(chain[str(r2)]) == str(r3)
              and str(chain[str(r3)]) == str(r4), str(chain))
        check("the last round points nowhere (goes to the human decision)",
              chain[str(r4)] is None)

    # ── Validation + coverage ────────────────────────────────────────────
    print("\n--- validation ---")
    async with f() as db:
        rep = await validate(db, wf, PROFILE)
        check("draft is publishable", rep.publishable, str(rep.errors))
        gap = [w for w in rep.warnings if "Version control" in w]
        check("coverage warns about the unassessed competency", len(gap) == 1, str(rep.warnings))
        dupe = [w for w in rep.warnings if "Problem solving" in w]
        check("coverage flags a competency assessed in 3 rounds", len(dupe) == 1, str(rep.warnings))
        check("coverage row count matches the profile", len(rep.coverage) == 5)
        check("a gap is a warning, not a blocker", rep.publishable and rep.warnings)

    # Structural failures are blockers.
    bad_cycle = [
        {"id": "a", "position": 0, "title": "A", "kind": "mcq", "pass_threshold": 50,
         "exam_round_id": "x", "on_pass_next_round_id": "b"},
        {"id": "b", "position": 1, "title": "B", "kind": "mcq", "pass_threshold": 50,
         "exam_round_id": "x", "on_pass_next_round_id": "a"},
    ]
    errs = validate_chain(bad_cycle)
    check("a cycle is refused", any("loop" in e for e in errs), str(errs))

    orphan = [
        {"id": "a", "position": 0, "title": "A", "kind": "mcq", "pass_threshold": 50,
         "exam_round_id": "x", "on_pass_next_round_id": None},
        {"id": "b", "position": 1, "title": "Stranded", "kind": "mcq", "pass_threshold": 50,
         "exam_round_id": "x", "on_pass_next_round_id": None},
    ]
    errs = validate_chain(orphan)
    check("an unreachable round is refused", any("Unreachable" in e for e in errs), str(errs))

    no_questions = [
        {"id": "a", "position": 0, "title": "Aptitude", "kind": "mcq", "pass_threshold": 50,
         "exam_round_id": None, "on_pass_next_round_id": None},
    ]
    errs = validate_chain(no_questions)
    check("an exam round with no questions is refused",
          any("needs questions" in e for e in errs), str(errs))

    async with f() as db:
        rid2 = uuid.uuid4()
        await db.execute(text(
            "INSERT INTO job_requisitions (id,company_id,title,created_at,updated_at)"
            " VALUES (:i,:c,'Staff Nurse',now(),now())"), {"i": rid2, "c": cid})
        empty_wf = await create_draft(db, company_id=cid, requisition_id=rid2, created_by=uid)
        await db.commit()
        rep_empty = await validate(db, empty_wf, PROFILE)
        check("an empty workflow is refused", not rep_empty.publishable, str(rep_empty.errors))
        try:
            await publish(db, company_id=cid, workflow_id=empty_wf, profile_competencies=PROFILE)
            pub_blocked = not (await published_workflow(db, rid2))
        except WorkflowError:
            pub_blocked = True
        await db.rollback()
        check("publishing an invalid workflow is blocked", pub_blocked)

    # ── Publish + immutability ───────────────────────────────────────────
    print("\n--- publish and immutability ---")
    async with f() as db:
        rep = await publish(db, company_id=cid, workflow_id=wf, profile_competencies=PROFILE)
        await db.commit()
        check("publish succeeded", rep.publishable, str(rep.errors))
        live = await published_workflow(db, rid)
        check("published workflow is the live one", live and str(live["id"]) == str(wf))
        check("published_at stamped", live and live["published_at"] is not None)

        try:
            await add_round(db, company_id=cid, workflow_id=wf, title="Sneaky",
                            kind="human_review")
            await db.rollback()
            check("a published workflow cannot gain a round", False, "the edit was allowed")
        except WorkflowError as exc:
            await db.rollback()
            check("a published workflow cannot gain a round",
                  "Create a new version" in str(exc), str(exc))

        try:
            await set_round_criteria(db, company_id=cid, round_id=r3,
                                     criteria=[PROFILE[4]])
            await db.rollback()
            check("a published workflow's criteria cannot be rewritten", False, "allowed")
        except WorkflowError as exc:
            await db.rollback()
            check("a published workflow's criteria cannot be rewritten",
                  "Create a new version" in str(exc), str(exc))

    # ── Clone to edit ────────────────────────────────────────────────────
    print("\n--- clone for edit ---")
    async with f() as db:
        v2 = await clone_for_edit(db, company_id=cid, workflow_id=wf, created_by=uid)
        await db.commit()
        check("clone is version 2",
              (await db.scalar(text("SELECT version FROM workflows WHERE id=:i"),
                               {"i": v2})) == 2)
        check("clone is a draft",
              (await db.scalar(text("SELECT status FROM workflows WHERE id=:i"),
                               {"i": v2})) == "draft")
        check("v1 is still published",
              (await db.scalar(text("SELECT status FROM workflows WHERE id=:i"),
                               {"i": wf})) == "published")

        old_r, new_r = await load_rounds(db, wf), await load_rounds(db, v2)
        check("clone copied every round",
              [r["title"] for r in old_r] == [r["title"] for r in new_r], str(new_r))
        new_ids = {str(r["id"]) for r in new_r}
        old_ids = {str(r["id"]) for r in old_r}
        check("clone minted new round ids", not (new_ids & old_ids))
        nxt = {r["title"]: r["on_pass_next_round_id"] for r in new_r}
        by_id = {str(r["id"]): r["title"] for r in new_r}
        check("clone rewired the chain to its OWN rounds",
              by_id.get(str(nxt["Aptitude"])) == "Technical"
              and by_id.get(str(nxt["Technical"])) == "AI Interview"
              and nxt["HR Final"] is None, str(nxt))

        old_c = await load_criteria(db, [r["id"] for r in old_r])
        new_c = await load_criteria(db, [r["id"] for r in new_r])
        check("clone copied the criteria",
              sorted(c["competency_id"] for v in old_c.values() for c in v)
              == sorted(c["competency_id"] for v in new_c.values() for c in v))

        # Publishing v2 must archive v1, never leave two live.
        await publish(db, company_id=cid, workflow_id=v2, profile_competencies=PROFILE)
        await db.commit()
        live_count = await db.scalar(text(
            "SELECT count(*) FROM workflows WHERE requisition_id=:r AND status='published'"),
            {"r": rid})
        check("exactly one published workflow after the second publish", live_count == 1,
              f"count={live_count}")
        check("v1 archived rather than deleted",
              (await db.scalar(text("SELECT status FROM workflows WHERE id=:i"),
                               {"i": wf})) == "archived")
        v1_rounds = await load_rounds(db, wf)
        check("an enrolled candidate could still run v1", len(v1_rounds) == 4,
              f"{len(v1_rounds)} rounds")

    # ── Criteria are frozen, not referenced ──────────────────────────────
    print("\n--- frozen criteria ---")
    async with f() as db:
        stored = await load_criteria(db, [r3])
        got = stored.get(str(r3), [])
        check("criteria stored with name and weight, not just an id",
              all(c["competency_name"] and c["weight"] > 0 for c in got), str(got))
        check("a round's criteria are exactly what was selected",
              sorted(c["competency_id"] for c in got)
              == ["communication", "problem_solving", "python_proficiency"], str(got))
        # The profile could be re-derived with different competencies tomorrow;
        # the published rubric must not move.
        check("no foreign key ties criteria to a live profile",
              (await db.scalar(text(
                  "SELECT count(*) FROM information_schema.table_constraints"
                  " WHERE table_name='round_criteria' AND constraint_type='FOREIGN KEY'"))) == 1,
              "only the round FK should exist")

    # ── The absence that matters (D-05) ──────────────────────────────────
    print("\n--- D-05: nothing here can end a candidacy ---")
    async with f() as db:
        cols = (await db.execute(text(
            "SELECT column_name FROM information_schema.columns"
            " WHERE table_name='workflows'"))).scalars().all()
        banned = [c for c in cols if "reject" in c.lower() or "auto_decline" in c.lower()]
        check("no workflow setting can reject anyone", banned == [], str(banned))
        rk = (await db.execute(text(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint"
            " WHERE conname='ck_workflow_rounds_kind'"))).scalar()
        check("only the four agreed round kinds are legal",
              all(k in rk for k in ("mcq", "coding", "ai_interview", "human_review")), str(rk))

    await eng.dispose()
    print(f"\n{'=' * 64}\n  {len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("  FAILED:", ", ".join(FAIL))
    print("=" * 64)
    raise SystemExit(1 if FAIL else 0)


asyncio.run(main())
