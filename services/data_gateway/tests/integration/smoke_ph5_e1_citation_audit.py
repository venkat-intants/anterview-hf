"""PH5 Wave 3 (E1) — /agent/chat over real HTTP, with a fake LLM: citations get
run-scoped refs, an invented marker is stripped, evidence_used is computed, and
one agent.chat.answered audit row lands with no candidate name in it.

    docker run -d --name intants-pgv -e POSTGRES_PASSWORD=postgres \
      -e POSTGRES_DB=intants_smoke -p 55432:5432 pgvector/pgvector:pg16
    cd services/data_gateway
    export DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:55432/intants_smoke
    python -m alembic upgrade head
    PYTHONPATH=".;../.." python tests/integration/smoke_ph5_e1_citation_audit.py

This environment's own ph3-verify/ph3 convention differs from the hardcoded
URL above (see smoke_group_d_copilot.py's identical note) — read DATABASE_URL
from the environment instead of hardcoding it, so the same script runs against
either.

Exits non-zero on any failed check.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import UTC, datetime

from httpx import ASGITransport, AsyncClient
from shared.agents import AssistantStep, ToolCall
from shared.auth.base import User
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.routers.agent as agent_router
from app.database import get_db_session
from app.dependencies import get_current_user
from app.main import app

URL = os.environ.get(
    "DATABASE_URL", "postgresql+asyncpg://postgres:postgres@127.0.0.1:55432/intants_smoke"
)
PASS, FAIL = [], []


def check(label, cond, detail=""):
    (PASS if cond else FAIL).append(label)
    print(f"  {'PASS' if cond else 'FAIL'}  {label}{(' — ' + detail) if detail and not cond else ''}")


async def _fake_llm(system, messages, tools):
    """A scripted model: call list_applicants once, then answer citing a real
    ref AND an invented one — the server must strip only the invented one."""
    if not any(m.role == "tool" for m in messages):
        return AssistantStep(tool_calls=[ToolCall(name="list_applicants", arguments={})])
    return AssistantStep(
        text="Asha[S1] is the strongest candidate, per policy[S9]."
    )


async def main() -> None:
    eng = create_async_engine(URL)
    factory = async_sessionmaker(eng, expire_on_commit=False)
    now = datetime.now(tz=UTC)

    cid = uuid.uuid4()
    hr_uid = uuid.uuid4()
    applicant_id = uuid.uuid4()

    async with factory() as db:
        # Idempotent re-runs against a shared disposable database — no TRUNCATE
        # (this script does not own the whole schema the way the group smokes
        # do), just clear out a prior run's fixture by its stable slug.
        # audit_log itself is append-only (a DB trigger refuses DELETE, by
        # design — DPDP audit integrity), so old rows from a prior run are left
        # in place; the before/after count below is unaffected by that.
        await db.execute(text("DELETE FROM applicants WHERE email = 'asha@candidate.test'"))
        await db.execute(text("DELETE FROM users WHERE email = 'hr-e1@acme.test'"))
        await db.execute(text("DELETE FROM companies WHERE slug = 'acme-e1-audit'"))
        await db.commit()

    async with factory() as db:
        await db.execute(
            text(
                "INSERT INTO companies (id,name,slug,is_active,created_at,updated_at)"
                " VALUES (:i,'Acme E1','acme-e1-audit',true,:t,:t)"
            ),
            {"i": cid, "t": now},
        )
        await db.execute(
            text(
                "INSERT INTO users (id,email,full_name,password_hash,company_id,"
                " preferred_language,is_active,notify_login_email,must_change_password,"
                " created_at,updated_at)"
                " VALUES (:i,'hr-e1@acme.test','HR E1','x',:c,'en',true,false,false,:t,:t)"
            ),
            {"i": hr_uid, "c": cid, "t": now},
        )
        await db.execute(
            text(
                "INSERT INTO applicants (id,company_id,full_name,email,target_job_title,"
                " target_level,status,created_at,updated_at,resume_text)"
                " VALUES (:i,:c,'Asha Applicant','asha@candidate.test','Welder','mid',"
                " 'new',:t,:t,'5 years welding.')"
            ),
            {"i": applicant_id, "c": cid, "t": now},
        )
        await db.commit()

    async def _db_override():
        async with factory() as s:
            yield s

    app.dependency_overrides[get_db_session] = _db_override
    app.dependency_overrides[get_current_user] = lambda: User(
        user_id=str(hr_uid), full_name="HR E1", email="hr-e1@acme.test", roles=["hr_manager"],
    )
    agent_router.build_agent_llm = lambda: _fake_llm

    async with factory() as db:
        before_count = await db.scalar(
            text("SELECT count(*) FROM audit_log WHERE action = 'agent.chat.answered'")
        )

    tr = ASGITransport(app=app)
    async with AsyncClient(transport=tr, base_url="http://t") as c:
        r = await c.post("/agent/chat", json={"message": "who should I interview?"})
        check("POST /agent/chat -> 200", r.status_code == 200, r.text[:300])
        body = r.json()

        check("a citation was produced", len(body["citations"]) >= 1, str(body["citations"]))
        check(
            "the citation carries a non-empty ref",
            all(c["ref"] for c in body["citations"]),
            str(body["citations"]),
        )
        check("evidence_used is true", body["evidence_used"] is True)
        check("the valid marker survives in the reply", "[S1]" in body["reply"], body["reply"])
        check(
            "the invented marker is stripped from the reply",
            "[S9]" not in body["reply"],
            body["reply"],
        )
        check(
            "the reply does not leak the candidate's resume",
            "welding" not in body["reply"].lower(),
            body["reply"],
        )

    async with factory() as db:
        after_count = await db.scalar(
            text("SELECT count(*) FROM audit_log WHERE action = 'agent.chat.answered'")
        )
        check("exactly one new audit row was written", after_count - before_count == 1,
              f"before={before_count} after={after_count}")

        row = (
            await db.execute(
                text(
                    "SELECT actor_id, resource_type, resource_id, details FROM audit_log"
                    " WHERE action = 'agent.chat.answered' AND actor_id = :hr"
                    # event_id is a random UUID (not time-ordered) and event_ts
                    # is not always set, so THIS run's own actor_id — fresh
                    # every run — is what makes the row unambiguous, not an
                    # ORDER BY that assumed either column sorted chronologically.
                ),
                {"hr": hr_uid},
            )
        ).mappings().first()
        check("the audit row exists", row is not None)
        if row is not None:
            check("actor_id is the caller", str(row["actor_id"]) == str(hr_uid))
            check("resource_type is 'user'", row["resource_type"] == "user")
            check("resource_id is the caller, not the applicant", str(row["resource_id"]) == str(hr_uid))
            details = row["details"]
            check("details carries the console", details.get("console") == "hr_manager")
            check("details carries invented_refs = 1", details.get("invented_refs") == 1)
            check("details carries evidence_used = true", details.get("evidence_used") is True)
            check("details names the tool used", "list_applicants" in details.get("tools", []))
            check(
                "details carries the citation's kind and id, never a label",
                all("label" not in c for c in details.get("citations", [])),
                str(details.get("citations")),
            )
            blob = str(details)
            check("the audit row never carries the candidate's name", "Asha" not in blob, blob)
            check("the audit row never carries the reply text", body["reply"] not in blob, blob)

    app.dependency_overrides.clear()
    del agent_router.build_agent_llm
    await eng.dispose()
    print(f"\n{'=' * 60}\n  {len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("  FAILED:", ", ".join(FAIL))
    print("=" * 60)
    raise SystemExit(1 if FAIL else 0)


asyncio.run(main())
