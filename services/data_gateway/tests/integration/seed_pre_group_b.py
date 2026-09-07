"""Seed pre-Group-B data so the backfill has something realistic to chew on.

Deliberately messy, because the real data will be:
  * the same opening spelled three ways ("Python Developer", "python developer",
    "Python  Developer") — must collapse to ONE requisition
  * two genuinely different openings at the same company
  * the same person applying twice with the same email — a merge candidate the
    backfill must NOT resolve on its own
  * a second company with a colliding title — must stay separate
  * a soft-deleted applicant — must be ignored
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

URL = "postgresql+asyncpg://postgres:postgres@127.0.0.1:55432/intants_smoke"


async def main() -> None:
    eng = create_async_engine(URL)
    f = async_sessionmaker(eng, expire_on_commit=False)
    now = datetime.now(tz=UTC)

    async with eng.begin() as c:
        await c.execute(text(
            "TRUNCATE applicants, companies, users, jobs, exams, exam_rounds, exam_assignments,"
            " interview_invites, sessions, scorecards, email_events, notifications,"
            " reconciliation_state CASCADE"))

    acme, globex = uuid.uuid4(), uuid.uuid4()
    hr_a, hr_g = uuid.uuid4(), uuid.uuid4()
    exam_id, round_id, job_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

    async with f() as db:
        for cid, name, slug in [(acme, "Acme", "acme"), (globex, "Globex", "globex")]:
            await db.execute(text(
                "INSERT INTO companies (id,name,slug,is_active,created_at,updated_at)"
                " VALUES (:i,:n,:s,true,:t,:t)"), {"i": cid, "n": name, "s": slug, "t": now})
        for uid, cid, em in [(hr_a, acme, "hr@acme.test"), (hr_g, globex, "hr@globex.test")]:
            await db.execute(text(
                "INSERT INTO users (id,email,full_name,password_hash,company_id,preferred_language,"
                " is_active,notify_login_email,must_change_password,created_at,updated_at)"
                " VALUES (:i,:e,'HR','x',:c,'en',true,false,false,:t,:t)"),
                {"i": uid, "e": em, "c": cid, "t": now})
        await db.execute(text(
            "INSERT INTO jobs (id,title,description,level,created_at,updated_at)"
            " VALUES (:i,'Python Developer','d','mid',:t,:t)"), {"i": job_id, "t": now})
        await db.execute(text(
            "INSERT INTO exams (id,company_id,title,created_by_user_id,created_at,updated_at)"
            " VALUES (:i,:c,'Screen',:u,:t,:t)"), {"i": exam_id, "c": acme, "u": hr_a, "t": now})
        await db.execute(text(
            "INSERT INTO exam_rounds (id,exam_id,company_id,round_number,title,position,created_at,updated_at)"
            " VALUES (:i,:e,:c,1,'R1',0,:t,:t)"),
            {"i": round_id, "e": exam_id, "c": acme, "t": now})

        # (company, name, email, title_as_typed, status, ats, deleted, age_days)
        people = [
            (acme, "Asha",   "asha@x.test",   "Python Developer",   "shortlisted", 8,    False, 30),
            (acme, "Bharat", "bharat@x.test", "python developer",   "new",         None, False, 25),
            (acme, "Chitra", "chitra@x.test", "Python  Developer",  "hired",       9,    False, 20),
            (acme, "Divya",  "divya@x.test",  "  Python Developer ", "rejected",   4,    False, 18),
            (acme, "Esha",   "esha@x.test",   "Staff Nurse",        "new",         7,    False, 15),
            (acme, "Farid",  "farid@x.test",  "Staff Nurse",        "interviewed", 6,    False, 12),
            # Same person, same email, two different openings -> merge candidate.
            (acme, "Gita",   "gita@x.test",   "Python Developer",   "new",         5,    False, 10),
            (acme, "Gita",   "gita@x.test",   "Staff Nurse",        "new",         6,    False, 9),
            # Same title at a DIFFERENT company -> must be its own requisition.
            (globex, "Hari", "hari@x.test",   "Python Developer",   "new",         7,    False, 8),
            # Soft-deleted -> ignored entirely.
            (acme, "Ivan",   "ivan@x.test",   "Ghost Role",         "new",         None, True,  5),
        ]
        ids = []
        for cid, name, email, title, status, ats, deleted, age in people:
            aid = uuid.uuid4()
            ids.append((aid, name, title))
            await db.execute(text(
                "INSERT INTO applicants (id,company_id,created_by_user_id,full_name,email,"
                " target_job_title,target_level,target_jd_text,resume_text,resume_s3_key,"
                " ats_overall,ats_recommendation,status,created_at,updated_at,deleted_at)"
                " VALUES (:i,:c,:u,:fn,:em,:tt,'mid','jd','cv',:key,:ats,:rec,:st,:t,:t,:d)"),
                {"i": aid, "c": cid, "u": hr_a if cid == acme else hr_g, "fn": name, "em": email,
                 "tt": title, "key": f"applicants/{cid}/{aid}.pdf", "ats": ats,
                 "rec": "interview" if ats else None, "st": status,
                 "t": now - timedelta(days=age), "d": now if deleted else None})

        # An exam assignment and an interview invite that must get re-pointed.
        await db.execute(text(
            "INSERT INTO exam_assignments (id,company_id,exam_id,round_id,applicant_id,"
            " created_by_user_id,token_hash,expires_at,status,created_at,updated_at)"
            " VALUES (:i,:c,:e,:r,:a,:u,:th,:x,'invited',:t,:t)"),
            {"i": uuid.uuid4(), "c": acme, "e": exam_id, "r": round_id, "a": ids[0][0],
             "u": hr_a, "th": uuid.uuid4().hex, "x": now + timedelta(days=2), "t": now})
        await db.execute(text(
            "INSERT INTO interview_invites (id,company_id,applicant_id,job_id,created_by_user_id,"
            " token_hash,language,expires_at,status,created_at,updated_at)"
            " VALUES (:i,:c,:a,:j,:u,:th,'en',:x,'invited',:t,:t)"),
            {"i": uuid.uuid4(), "c": acme, "a": ids[2][0], "j": job_id, "u": hr_a,
             "th": uuid.uuid4().hex, "x": now + timedelta(days=2), "t": now})
        await db.commit()

    print(f"seeded {len(people)} applicants across 2 companies")
    await eng.dispose()


asyncio.run(main())
