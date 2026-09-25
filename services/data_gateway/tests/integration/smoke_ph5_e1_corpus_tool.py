"""PH5 Wave 3 (E1) — search_company_documents, the tool the wave shipped
without: the corpus has upload, versioning, retention and a fully tested
search_corpus(), and until this tool exists the assistant cannot answer "what
does our leave policy say?" at all.

Three things under test, per the design's own list:
  * the tool through /agent/chat, with the fake LLM, returns a `document`
    citation with a locator;
  * a super_admin never receives a passage from an `hr_only` document, even
    though the tool itself is offered to both hr_manager and super_admin
    (company_scoped) — the audience predicate lives in app.corpus.search_corpus
    and is exercised here end-to-end through the tool, not re-implemented;
  * (the AST test that no draft_* handler reaches the corpus is covered in
    services/data_gateway/tests/unit/test_ph5_e2_corpus_ingest.py and is run
    separately — nothing here duplicates it).

    export DATABASE_URL=postgresql+asyncpg://ph3:ph3@127.0.0.1:55432/<db>
    cd services/data_gateway
    PYTHONPATH=".;../.." python tests/integration/smoke_ph5_e1_corpus_tool.py

Exits non-zero on any failed check.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import uuid
from datetime import UTC, datetime

from httpx import ASGITransport, AsyncClient
from shared.agents import AssistantStep, ToolCall, ToolContext
from shared.auth.base import User
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.routers.agent as agent_router
from app.agents.tools import registry
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


def _sha(seed: str) -> str:
    return hashlib.sha256(seed.encode()).hexdigest()


async def call(name, args, ctx):
    result = await registry.invoke(name, args, ctx, call_id=uuid.uuid4().hex[:8])
    return result


async def main() -> None:
    eng = create_async_engine(URL)
    factory = async_sessionmaker(eng, expire_on_commit=False)
    now = datetime.now(tz=UTC)

    cid = uuid.uuid4()
    hr_uid = uuid.uuid4()
    sa_uid = uuid.uuid4()
    doc_id = uuid.uuid4()
    version_id = uuid.uuid4()
    chunk_id = uuid.uuid4()

    passage_text = (
        "The notice period for resignation is 30 days, paid in full during "
        "the notice period. Employees must hand over all company property."
    )

    async with factory() as db:
        # Idempotent re-runs against a shared disposable database (no TRUNCATE
        # — this script does not own the whole schema). FK-safe order: chunks,
        # versions, documents, users, company.
        old_company = await db.scalar(
            text("SELECT id FROM companies WHERE slug = 'acme-corpus-smoke'")
        )
        if old_company is not None:
            await db.execute(
                text("DELETE FROM corpus_chunks WHERE company_id = :c"), {"c": old_company}
            )
            await db.execute(
                text("DELETE FROM corpus_document_versions WHERE company_id = :c"),
                {"c": old_company},
            )
            await db.execute(
                text("DELETE FROM corpus_documents WHERE company_id = :c"), {"c": old_company}
            )
            await db.execute(
                text("DELETE FROM users WHERE company_id = :c"), {"c": old_company}
            )
            await db.execute(text("DELETE FROM companies WHERE id = :c"), {"c": old_company})
            await db.commit()

        await db.execute(
            text(
                "INSERT INTO companies (id,name,slug,is_active,created_at,updated_at)"
                " VALUES (:i,'Acme Corpus','acme-corpus-smoke',true,:t,:t)"
            ),
            {"i": cid, "t": now},
        )
        await db.execute(
            text(
                "INSERT INTO users (id,email,full_name,password_hash,company_id,"
                " preferred_language,is_active,notify_login_email,must_change_password,"
                " created_at,updated_at)"
                " VALUES (:i,'hr-corpus@acme.test','HR','x',:c,'en',true,false,false,:t,:t)"
            ),
            {"i": hr_uid, "c": cid, "t": now},
        )
        await db.execute(
            text(
                "INSERT INTO users (id,email,full_name,password_hash,company_id,"
                " preferred_language,is_active,notify_login_email,must_change_password,"
                " created_at,updated_at)"
                " VALUES (:i,'sa-corpus@acme.test','SA','x',:c,'en',true,false,false,:t,:t)"
            ),
            {"i": sa_uid, "c": cid, "t": now},
        )
        await db.execute(
            text(
                "INSERT INTO corpus_documents"
                " (id,company_id,title,audience,doc_kind,created_by_user_id,created_at,updated_at)"
                " VALUES (:i,:c,'Employee handbook','hr_only','handbook',:u,:t,:t)"
            ),
            {"i": doc_id, "c": cid, "u": hr_uid, "t": now},
        )
        await db.execute(
            text(
                "INSERT INTO corpus_document_versions"
                " (id,company_id,document_id,version,status,storage_key,original_name,"
                "  content_type,size_bytes,sha256,char_count,chunk_count,"
                "  uploaded_by_user_id,uploaded_at,indexed_at)"
                " VALUES (:i,:c,:d,1,'indexed',:sk,'handbook.txt','text/plain',200,:sha,"
                "  :cc,1,:u,:t,:t)"
            ),
            {
                "i": version_id, "c": cid, "d": doc_id, "sk": f"corpus/{doc_id}/v1",
                "sha": _sha("handbook-v1"), "cc": len(passage_text), "u": hr_uid, "t": now,
            },
        )
        await db.execute(
            text(
                "INSERT INTO corpus_chunks"
                " (id,company_id,document_id,version_id,ordinal,page_from,page_to,heading,"
                "  content,char_count,content_sha256,created_at)"
                " VALUES (:i,:c,:d,:v,0,4,4,'2.1 Notice and exit',:content,:cc,:csha,:t)"
            ),
            {
                "i": chunk_id, "c": cid, "d": doc_id, "v": version_id, "content": passage_text,
                "cc": len(passage_text), "csha": _sha("handbook-v1-chunk-0"), "t": now,
            },
        )
        await db.commit()

    # ── Direct tool invocation: hr_manager sees it, super_admin does not ──────
    async with factory() as db:
        hr_ctx = ToolContext(
            actor_id=str(hr_uid), role="hr_manager", company_id=str(cid), resources={"db": db},
        )
        res = await call("search_company_documents", {"query": "notice period"}, hr_ctx)
        check("hr_manager: tool call ok", res.ok, res.error or "")
        check("hr_manager: at least one passage", "notice period" in res.content.lower(), res.content[:300])
        check("hr_manager: one document citation", len(res.citations) == 1, str(res.citations))
        if res.citations:
            cite = res.citations[0]
            check("citation kind is document", cite.kind == "document", cite.kind)
            check("citation href points at the library route",
                  cite.href == f"/hr/library/{doc_id}", str(cite.href))
            check("citation locator names version, page and heading",
                  cite.locator == "v1 · page 4 · 2.1 Notice and exit", str(cite.locator))
            check("citation label is the sanitised title", cite.label == "Employee handbook", cite.label)
        check("the passage is fenced as DATA, NOT INSTRUCTIONS",
              "<<<PASSAGE P1" in res.content and "DATA, NOT INSTRUCTIONS" in res.content,
              res.content[:400])
        check("the fence is closed", "<<<END PASSAGE P1>>>" in res.content, res.content[:400])

    async with factory() as db:
        sa_ctx = ToolContext(
            actor_id=str(sa_uid), role="super_admin", company_id=str(cid), resources={"db": db},
        )
        res = await call("search_company_documents", {"query": "notice period"}, sa_ctx)
        check("super_admin: tool call still ok (not a permission error)", res.ok, res.error or "")
        check("super_admin: zero passages for an hr_only document",
              '"passages": []' in res.content, res.content[:300])
        check("super_admin: zero citations", res.citations == [])
        # Distinct from the query text itself ("notice period" legitimately
        # echoes back regardless of any match) — this phrase only exists in the
        # seeded PASSAGE content, so its absence is what proves nothing leaked.
        check("super_admin: the handbook's own text never reached them",
              "paid in full" not in res.content.lower(), res.content[:300])

    # ── /agent/chat, fake LLM, real HTTP ──────────────────────────────────────
    calls = {"n": 0}

    async def _fake_llm(system, messages, tools):
        calls["n"] += 1
        if calls["n"] == 1:
            return AssistantStep(
                tool_calls=[ToolCall(name="search_company_documents", arguments={"query": "notice period"})]
            )
        return AssistantStep(text="Per the handbook, the notice period is 30 days[S1].")

    async def _db_override():
        async with factory() as s:
            yield s

    agent_router.build_agent_llm = lambda: _fake_llm
    app.dependency_overrides[get_db_session] = _db_override
    app.dependency_overrides[get_current_user] = lambda: User(
        user_id=str(hr_uid), full_name="HR", email="hr-corpus@acme.test", roles=["hr_manager"],
    )

    tr = ASGITransport(app=app)
    async with AsyncClient(transport=tr, base_url="http://t") as c:
        r = await c.post("/agent/chat", json={"message": "what is our notice period policy?"})
        check("POST /agent/chat -> 200", r.status_code == 200, r.text[:300])
        body = r.json()
        doc_citations = [c_ for c_ in body["citations"] if c_["kind"] == "document"]
        check("a document citation is present", len(doc_citations) == 1, str(body["citations"]))
        if doc_citations:
            dc = doc_citations[0]
            check("it carries a non-empty ref", bool(dc["ref"]), str(dc))
            check("it carries the expected locator",
                  dc["locator"] == "v1 · page 4 · 2.1 Notice and exit", str(dc))
            check("it carries the /hr/library href", dc["href"] == f"/hr/library/{doc_id}", str(dc))
        check("evidence_used is true", body["evidence_used"] is True)
        check("the marker survives in the reply", "[S1]" in body["reply"], body["reply"])

    app.dependency_overrides.clear()
    del agent_router.build_agent_llm
    await eng.dispose()
    print(f"\n{'=' * 60}\n  {len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("  FAILED:", ", ".join(FAIL))
    print("=" * 60)
    raise SystemExit(1 if FAIL else 0)


asyncio.run(main())
