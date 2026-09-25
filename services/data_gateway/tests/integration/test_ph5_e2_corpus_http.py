"""PH5-E2 HTTP-level tests — the document library routes end to end, through
the real ASGI app and real JWTs (LOW-9, security review: no HTTP coverage of
these routes existed at all).

On the ``test_ph5_w1_metrics.py`` precedent: an in-process ``httpx.AsyncClient``
over ``ASGITransport``, running the app's own lifespan, against this repo's
disposable Postgres. Role checks (``require_role_password_ok``) read only the
JWT's ``roles`` claim, so the negative cases (interviewer, candidate) need no
seeded user row at all — the 403 fires before any query.
"""

from __future__ import annotations

import io
import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from shared.auth.jwt import issue_access_token
from shared.db.engine import build_engine, build_session_factory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import corpus as corpus_module
from app.config import settings

pytestmark = pytest.mark.integration


class _FakeStore:
    """In-memory stand-in for object storage — the test_ph4_d4_wave5_fixes.py
    / test_ph5_e2_corpus_db.py precedent. No network, no real S3 credentials
    needed, and this HTTP test runs the ASGI app IN-PROCESS, so patching the
    module the router imports actually reaches the request."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    async def store(self, _settings: object, key: str, data: bytes, _content_type: str) -> None:
        self.objects[key] = data

    async def remove(self, _settings: object, keys: list[str]) -> int:
        removed = 0
        for k in keys:
            if self.objects.pop(k, None) is not None:
                removed += 1
        return removed


@pytest_asyncio.fixture
async def committed_db() -> AsyncIterator[AsyncSession]:
    """A session that COMMITS — the ASGI app reads through its own connection
    and cannot see an uncommitted write on this one (the
    ``test_ph5_w1_metrics.py`` precedent). This repo's own disposable
    database; nothing here needs cleaning up, every test uses fresh ids."""
    engine = build_engine(database_url=settings.database_url, database_ssl=settings.database_ssl,
                          pool_size=2)
    factory = build_session_factory(engine)
    try:
        async with factory() as session:
            yield session
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    from app.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", timeout=30.0,
    ) as ac, app.router.lifespan_context(app):
        yield ac


def _token(user_id: uuid.UUID, roles: list[str]) -> str:
    return issue_access_token(
        str(user_id), roles, settings.jwt_secret, issuer=settings.jwt_issuer,
        audience=settings.jwt_audience,
    )


def _auth(user_id: uuid.UUID, roles: list[str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {_token(user_id, roles)}"}


async def _seed_company_and_staff(db: AsyncSession) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """(company_id, hr_manager_user_id, super_admin_user_id).

    This is a COMMITTED write (the ASGI app must see it), and the reconciler's
    corpus-embed pass — proven by HIGH-1 — scans across EVERY company with no
    tenant scoping at all, so a company this fixture leaves behind is not
    inert: it is one more row an unrelated test's exact-count assertion can
    trip over. Callers MUST clean up via ``_cleanup_company``.
    """
    company_id, hr_id, super_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    tag = company_id.hex[:10]
    await db.execute(
        text("INSERT INTO companies (id, name, slug) VALUES (:c, 'HTTP co', :s)"),
        {"c": company_id, "s": f"http-{tag}"},
    )
    await db.execute(
        text(
            "INSERT INTO users (id, email, company_id) VALUES"
            " (:h, :he, :c), (:s, :se, :c)"
        ),
        {"h": hr_id, "he": f"hr-{tag}@http.test", "s": super_id, "se": f"super-{tag}@http.test",
         "c": company_id},
    )
    await db.commit()
    return company_id, hr_id, super_id


async def _cleanup_company(db: AsyncSession, company_id: uuid.UUID,
                           user_ids: tuple[uuid.UUID, ...]) -> None:
    """Companion to ``_seed_company_and_staff`` — deleting ``companies``
    cascades to every corpus table (ON DELETE CASCADE), and ``users.company_id``
    is only ON DELETE SET NULL, so the user rows are removed explicitly by id."""
    await db.execute(text("DELETE FROM companies WHERE id = :c"), {"c": company_id})
    await db.execute(text("DELETE FROM users WHERE id = ANY(:u)"), {"u": list(user_ids)})
    await db.commit()


# ---------------------------------------------------------------------------
# Role gate — no company/user row needed at all: the 403 is on the JWT claim
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_interviewer_gets_403(client: AsyncClient) -> None:
    resp = await client.get("/hr/library", headers=_auth(uuid.uuid4(), ["interviewer"]))
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_candidate_gets_403(client: AsyncClient) -> None:
    # A candidate account carries no elevated role at all.
    resp = await client.get("/hr/library", headers=_auth(uuid.uuid4(), []))
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Q3 over HTTP, and the upload audit row
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_super_admin_uploading_hr_only_gets_422(
    client: AsyncClient, committed_db: AsyncSession,
) -> None:
    company_id, hr_id, super_id = await _seed_company_and_staff(committed_db)
    try:
        resp = await client.post(
            "/hr/library",
            headers=_auth(super_id, ["super_admin"]),
            files={"file": ("policy.txt", io.BytesIO(b"Some company text about leave."), "text/plain")},
            data={"title": "Comp bands", "audience": "hr_only", "doc_kind": "policy",
                  "attested": "true"},
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["failure_code"] == "hr_only_requires_hr_manager"

        count = await committed_db.scalar(
            text("SELECT count(*) FROM corpus_documents WHERE company_id = :c"), {"c": company_id}
        )
        assert count == 0  # no row created
    finally:
        await _cleanup_company(committed_db, company_id, (hr_id, super_id))


@pytest.mark.asyncio
async def test_hr_manager_upload_succeeds_and_writes_an_audit_row(
    client: AsyncClient, committed_db: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeStore()
    monkeypatch.setattr(corpus_module.store, "store", fake.store)
    monkeypatch.setattr(corpus_module.store, "remove", fake.remove)

    company_id, hr_id, super_id = await _seed_company_and_staff(committed_db)
    try:
        body = b"Employees accrue twenty days of paid annual leave. " * 3
        resp = await client.post(
            "/hr/library",
            headers=_auth(hr_id, ["hr_manager"]),
            files={"file": ("leave.txt", io.BytesIO(body), "text/plain")},
            data={"title": "Leave Policy", "audience": "all_staff", "doc_kind": "policy",
                  "attested": "true"},
        )
        assert resp.status_code == 201
        document_id = uuid.UUID(resp.json()["id"])

        audit_row = (
            await committed_db.execute(
                text(
                    "SELECT actor_id, details FROM audit_log"
                    " WHERE action = 'corpus.document.uploaded' AND resource_id = :d"
                ),
                {"d": document_id},
            )
        ).mappings().first()
        assert audit_row is not None
        assert audit_row["actor_id"] == hr_id
        assert audit_row["details"]["attested"] is True

        # And the interviewer/candidate role gate applies to every route on
        # this router, not only the list endpoint.
        resp_iv = await client.get(
            f"/hr/library/{document_id}", headers=_auth(uuid.uuid4(), ["interviewer"]),
        )
        assert resp_iv.status_code == 403
    finally:
        # Deleting the company cascades corpus_documents/_document_versions/
        # _chunks/_events -- the reconciler's corpus-embed pass scans across
        # every company with no tenant scoping (HIGH-1), so a document left
        # behind here is live ammunition for an unrelated exact-count
        # assertion elsewhere in the suite, not inert debris.
        await _cleanup_company(committed_db, company_id, (hr_id, super_id))
