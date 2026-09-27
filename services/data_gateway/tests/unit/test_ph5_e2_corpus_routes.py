"""PH5-E2 — the ``/hr/library`` router, without a database.

FastAPI dependency overrides (the ``tests/unit/test_hr_applicants.py`` idiom)
plus a stubbed service layer, so what is under test here is only what the router
itself owns and nothing else does:

* the status codes and the ``{"failure_code", "message"}`` body a refusal
  returns — the contract the library screen reads to decide what to say;
* WHO commits. Every function in ``app/corpus.py`` deliberately ends at
  ``flush()``; the router is the only place a transaction is closed, and a
  ``CorpusError`` must roll back BEFORE the response is raised. A missing commit
  on the download route would silently drop its audit row, which is the one
  thing that route writes;
* that the form/query/body arguments reach the service unchanged, including the
  ``max_bytes + 1`` read that lets an oversized file be REFUSED rather than
  silently truncated to the legal size.

The routes' real authorisation (``require_role_password_ok``) and their real
tenancy are overridden here on purpose; they are exercised end to end in
``tests/integration/test_ph5_e2_corpus_http.py``.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import pytest
import pytest_asyncio
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from shared.auth.base import User

from app import corpus as svc
from app.config import settings
from app.database import get_db_session
from app.main import app
from app.routers import hr_corpus
from tests.unit._corpus_fakes import FakeSession

COMPANY = uuid.uuid4()
ACTOR = uuid.uuid4()
DOC = uuid.uuid4()

DOCUMENT_OUT: dict[str, Any] = {
    "id": str(DOC), "title": "Employee handbook", "audience": "all_staff",
    "doc_kind": "handbook", "expires_on": None,
    "created_at": datetime.now(tz=UTC).isoformat(), "updated_at": datetime.now(tz=UTC).isoformat(),
    "version": 1, "status": "parsed", "failure_code": None, "original_name": "handbook.txt",
    "content_type": "text/plain", "size_bytes": 12, "page_count": None, "chunk_count": 2,
    "injection_markers": 0, "uploaded_at": datetime.now(tz=UTC).isoformat(),
    "uploaded_by_name": "Asha",
}


class Calls:
    """What the stubbed service layer was handed."""

    def __init__(self) -> None:
        self.kwargs: dict[str, Any] = {}
        self.positional: tuple[Any, ...] = ()


@pytest_asyncio.fixture
async def client() -> AsyncIterator[tuple[AsyncClient, FakeSession]]:
    """A client whose tenant context and session are stubs, so every assertion
    below is about the router. The session allows ``commit()`` (unlike the
    service-level fakes) precisely because committing is the router's job."""
    db = FakeSession(allow_commit=True)
    app.dependency_overrides[hr_corpus._corpus_ctx] = lambda: (ACTOR, COMPANY, "hr_manager")
    app.dependency_overrides[get_db_session] = lambda: db
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            yield ac, db
    finally:
        app.dependency_overrides.pop(hr_corpus._corpus_ctx, None)
        app.dependency_overrides.pop(get_db_session, None)


def stub(monkeypatch: pytest.MonkeyPatch, name: str, result: Any = None) -> Calls:
    """Replace one service function with a recorder. ``result`` may be an
    exception instance, which is raised."""
    calls = Calls()

    async def _fn(*args: Any, **kwargs: Any) -> Any:
        calls.positional = args
        calls.kwargs = kwargs
        if isinstance(result, BaseException):
            raise result
        return result

    monkeypatch.setattr(svc, name, _fn)
    return calls


# ---------------------------------------------------------------------------
# The tenant context every route depends on
# ---------------------------------------------------------------------------
def user(*roles: str, user_id: str | None = None) -> User:
    return User(
        user_id=user_id or str(ACTOR), full_name="Asha", email="asha@example.com",
        roles=list(roles),
    )


async def test_the_context_resolves_the_callers_company_and_role() -> None:
    db = FakeSession(routes=[("SELECT u.company_id FROM users u", [(COMPANY,)])])

    uid, company_id, role = await hr_corpus._corpus_ctx(user("hr_manager"), db)  # type: ignore[arg-type]

    assert (uid, company_id, role) == (ACTOR, COMPANY, "hr_manager")
    sql, params = db.one_statement("SELECT u.company_id FROM users u")
    assert "c.deleted_at IS NULL" in sql
    assert "u.deleted_at IS NULL" in sql
    assert params == {"uid": ACTOR}


async def test_a_caller_with_both_roles_is_treated_as_the_hr_manager() -> None:
    """``hr_manager`` is the wider audience of the two, and the service layer
    refuses ``hr_only`` to a super_admin — so resolving the pair the other way
    would lock an HR manager who also holds super_admin out of their own
    documents."""
    db = FakeSession(routes=[("SELECT u.company_id FROM users u", [(COMPANY,)])])

    _uid, _cid, role = await hr_corpus._corpus_ctx(user("super_admin", "hr_manager"), db)  # type: ignore[arg-type]

    assert role == "hr_manager"


async def test_a_super_admin_without_the_hr_role_is_a_super_admin() -> None:
    db = FakeSession(routes=[("SELECT u.company_id FROM users u", [(COMPANY,)])])

    _uid, _cid, role = await hr_corpus._corpus_ctx(user("super_admin"), db)  # type: ignore[arg-type]

    assert role == "super_admin"


async def test_an_account_with_no_active_company_cannot_reach_the_library() -> None:
    """The library is a company's own; without a company there is no tenancy to
    scope it to, and the join here is what makes a SOFT-DELETED company's staff
    fall into the same 403 rather than reading its documents."""
    db = FakeSession(routes=[("SELECT u.company_id FROM users u", [])])

    with pytest.raises(HTTPException) as caught:
        await hr_corpus._corpus_ctx(user("hr_manager"), db)  # type: ignore[arg-type]

    assert caught.value.status_code == 403


async def test_a_company_column_that_is_null_is_also_a_403() -> None:
    db = FakeSession(routes=[("SELECT u.company_id FROM users u", [(None,)])])

    with pytest.raises(HTTPException) as caught:
        await hr_corpus._corpus_ctx(user("hr_manager"), db)  # type: ignore[arg-type]

    assert caught.value.status_code == 403


async def test_an_identity_that_is_not_a_uuid_is_rejected_before_any_query() -> None:
    """Every corpus statement binds this id; letting a non-UUID through would
    turn a malformed token into a 500 from asyncpg."""
    db = FakeSession()

    with pytest.raises(HTTPException) as caught:
        await hr_corpus._corpus_ctx(user("hr_manager", user_id="not-a-uuid"), db)  # type: ignore[arg-type]

    assert caught.value.status_code == 400
    assert db.statements == []


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------
async def test_listing_the_library_passes_the_callers_role_to_the_service(
    client: tuple[AsyncClient, FakeSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    ac, db = client
    calls = stub(monkeypatch, "list_documents", [DOCUMENT_OUT])

    resp = await ac.get("/hr/library")

    assert resp.status_code == 200
    assert resp.json() == [DOCUMENT_OUT]
    assert calls.kwargs["company_id"] == COMPANY
    assert calls.kwargs["role"] == "hr_manager"
    assert db.commits == 0, "a read must not open and close a transaction"


async def test_a_document_the_service_will_not_show_is_a_404(
    client: tuple[AsyncClient, FakeSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    """``get_document`` returns None both for a document that is not there and
    for one this caller may not read — the router must not distinguish them,
    or the 404/403 split becomes a way to probe for hr_only documents."""
    ac, _db = client
    stub(monkeypatch, "get_document", None)

    resp = await ac.get(f"/hr/library/{DOC}")

    assert resp.status_code == 404


async def test_a_document_the_service_returns_is_passed_through_unchanged(
    client: tuple[AsyncClient, FakeSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The library screen reads these field names directly; the route must not
    reshape, rename or drop any of them on the way out."""
    ac, db = client
    calls = stub(monkeypatch, "get_document", DOCUMENT_OUT)

    resp = await ac.get(f"/hr/library/{DOC}")

    assert resp.status_code == 200
    assert resp.json() == DOCUMENT_OUT
    assert calls.kwargs["document_id"] == DOC
    assert calls.kwargs["role"] == "hr_manager"
    assert db.commits == 0


async def test_a_document_id_that_is_not_a_uuid_never_reaches_the_service(
    client: tuple[AsyncClient, FakeSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    ac, _db = client
    calls = stub(monkeypatch, "get_document", DOCUMENT_OUT)

    resp = await ac.get("/hr/library/not-a-uuid")

    assert resp.status_code == 422
    assert calls.kwargs == {}


# ---------------------------------------------------------------------------
# Upload and replace
# ---------------------------------------------------------------------------
async def test_an_upload_returns_201_and_commits_once(
    client: tuple[AsyncClient, FakeSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    ac, db = client
    calls = stub(monkeypatch, "ingest_document", DOCUMENT_OUT)

    resp = await ac.post(
        "/hr/library",
        data={"title": "Employee handbook", "audience": "hr_only", "doc_kind": "policy",
              "expires_on": "2027-01-31", "attested": "true"},
        files={"file": ("handbook.txt", b"# Leave\n\nTwenty days.", "text/plain")},
    )

    assert resp.status_code == 201
    assert resp.json() == DOCUMENT_OUT
    assert db.commits == 1
    assert calls.kwargs["title"] == "Employee handbook"
    assert calls.kwargs["audience"] == "hr_only"
    assert calls.kwargs["doc_kind"] == "policy"
    assert calls.kwargs["expires_on"].isoformat() == "2027-01-31"
    assert calls.kwargs["attested"] is True
    assert calls.kwargs["data"] == b"# Leave\n\nTwenty days."
    assert calls.kwargs["filename"] == "handbook.txt"
    assert calls.kwargs["company_id"] == COMPANY
    assert calls.kwargs["actor"] == ACTOR
    assert calls.kwargs["actor_role"] == "hr_manager"


async def test_an_upload_defaults_to_unattested_so_the_server_refuses_it(
    client: tuple[AsyncClient, FakeSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The attestation defaults to False, not True: a client that forgets the
    field gets the refusal, not a document nobody vouched for. (The refusal
    itself is ``app/corpus.py``'s, unit-tested next door.)"""
    ac, _db = client
    calls = stub(monkeypatch, "ingest_document", DOCUMENT_OUT)

    await ac.post(
        "/hr/library",
        data={"title": "Handbook", "audience": "all_staff"},
        files={"file": ("h.txt", b"text", "text/plain")},
    )

    assert calls.kwargs["attested"] is False
    assert calls.kwargs["doc_kind"] == "other"
    assert calls.kwargs["expires_on"] is None


async def test_an_upload_missing_its_file_or_title_is_a_422(
    client: tuple[AsyncClient, FakeSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    ac, _db = client
    calls = stub(monkeypatch, "ingest_document", DOCUMENT_OUT)

    no_file = await ac.post("/hr/library", data={"title": "T", "audience": "all_staff"})
    no_title = await ac.post(
        "/hr/library", data={"audience": "all_staff"},
        files={"file": ("h.txt", b"text", "text/plain")},
    )

    assert (no_file.status_code, no_title.status_code) == (422, 422)
    assert calls.kwargs == {}


async def test_an_oversized_upload_arrives_one_byte_over_the_cap_not_truncated_to_it(
    client: tuple[AsyncClient, FakeSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ``+ 1`` in the router's ``file.read``. Reading exactly ``max_bytes``
    would hand the service a payload that is the legal size by construction, so
    ``too_large`` could never fire and an over-cap document would be silently
    indexed from its first 10 MB."""
    ac, _db = client
    monkeypatch.setattr(settings, "corpus_document_max_bytes", 32)
    calls = stub(monkeypatch, "ingest_document", DOCUMENT_OUT)

    await ac.post(
        "/hr/library",
        data={"title": "Handbook", "audience": "all_staff", "attested": "true"},
        files={"file": ("big.txt", b"y" * 200, "text/plain")},
    )

    assert len(calls.kwargs["data"]) == 33
    assert len(calls.kwargs["data"]) > settings.corpus_document_max_bytes


async def test_a_replace_upload_is_a_post_to_the_documents_versions_collection(
    client: tuple[AsyncClient, FakeSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A new version is a creation, so 201 — and the document id comes from the
    path, never from the form, so a caller cannot post a version onto a
    different document than the one it addressed."""
    ac, db = client
    calls = stub(monkeypatch, "add_version", DOCUMENT_OUT)

    resp = await ac.post(
        f"/hr/library/{DOC}/versions",
        data={"document_id": str(uuid.uuid4())},
        files={"file": ("handbook-v2.txt", b"revised text", "text/plain")},
    )

    assert resp.status_code == 201
    assert db.commits == 1
    assert calls.kwargs["document_id"] == DOC
    assert calls.kwargs["data"] == b"revised text"


# ---------------------------------------------------------------------------
# Edit, delete, reindex
# ---------------------------------------------------------------------------
async def test_an_edit_validates_the_audience_at_the_boundary(
    client: tuple[AsyncClient, FakeSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The body's pattern is the outer gate; ``app/corpus.py`` refuses an unknown
    audience too. Both, on purpose — the router's is what keeps a junk value out
    of the service's own vocabulary check."""
    ac, _db = client
    calls = stub(monkeypatch, "update_document", DOCUMENT_OUT)

    resp = await ac.patch(f"/hr/library/{DOC}", json={"audience": "everyone"})

    assert resp.status_code == 422
    assert calls.kwargs == {}


async def test_an_edit_sends_both_fields_as_the_documents_new_state(
    client: tuple[AsyncClient, FakeSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    """``expires_on`` omitted means None, which the service reads as "no
    expiry" — the documented PUT-shaped contract, visible here as the router
    passing None rather than dropping the argument."""
    ac, db = client
    calls = stub(monkeypatch, "update_document", DOCUMENT_OUT)

    resp = await ac.patch(f"/hr/library/{DOC}", json={"audience": "hr_only"})

    assert resp.status_code == 200
    assert db.commits == 1
    assert calls.kwargs["audience"] == "hr_only"
    assert calls.kwargs["expires_on"] is None


async def test_a_delete_returns_204_with_no_body(
    client: tuple[AsyncClient, FakeSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    ac, db = client
    calls = stub(monkeypatch, "delete_document", None)

    resp = await ac.delete(f"/hr/library/{DOC}")

    assert resp.status_code == 204
    assert resp.content == b""
    assert db.commits == 1
    assert calls.kwargs["document_id"] == DOC


async def test_a_reindex_returns_the_documents_new_state(
    client: tuple[AsyncClient, FakeSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    ac, db = client
    stub(monkeypatch, "reindex_document", {**DOCUMENT_OUT, "status": "parsed"})

    resp = await ac.post(f"/hr/library/{DOC}/reindex")

    assert resp.status_code == 200
    assert resp.json()["status"] == "parsed"
    assert db.commits == 1


# ---------------------------------------------------------------------------
# Download — a GET that has to commit
# ---------------------------------------------------------------------------
async def test_the_download_get_commits_so_its_audit_row_persists(
    client: tuple[AsyncClient, FakeSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    """MEDIUM-4. ``download_url`` adds the ``corpus.document.downloaded`` row
    with ``db.add()`` and nothing else on this path writes — so without this
    commit the one record of who read a document's CONTENT is discarded at the
    end of the request."""
    ac, db = client
    stub(monkeypatch, "download_url", {"url": "https://signed.example/x", "expires_in": 300})

    resp = await ac.get(f"/hr/library/{DOC}/download")

    assert resp.status_code == 200
    assert resp.json()["url"] == "https://signed.example/x"
    assert db.commits == 1


async def test_a_download_can_ask_for_an_earlier_version(
    client: tuple[AsyncClient, FakeSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    ac, _db = client
    calls = stub(monkeypatch, "download_url", {"url": "u", "expires_in": 300})

    resp = await ac.get(f"/hr/library/{DOC}/download", params={"version": 3})

    assert resp.status_code == 200
    assert calls.kwargs["version"] == 3


@pytest.mark.parametrize("version", ["0", "-1", "abc"])
async def test_a_nonsense_version_is_refused_at_the_boundary(
    client: tuple[AsyncClient, FakeSession], monkeypatch: pytest.MonkeyPatch, version: str
) -> None:
    """Versions start at 1. Letting 0 through would build a join predicate that
    matches nothing and return a 404 that looks like a permission problem."""
    ac, _db = client
    calls = stub(monkeypatch, "download_url", {"url": "u", "expires_in": 300})

    resp = await ac.get(f"/hr/library/{DOC}/download", params={"version": version})

    assert resp.status_code == 422
    assert calls.kwargs == {}


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------
async def test_search_passes_the_query_and_limit_through_with_the_session_role(
    client: tuple[AsyncClient, FakeSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    ac, _db = client
    calls = stub(monkeypatch, "search_corpus", {"semantic": True, "passages": []})

    resp = await ac.post("/hr/library/search", json={"query": "notice period", "limit": 6})

    assert resp.status_code == 200
    assert resp.json() == {"semantic": True, "passages": []}
    assert calls.kwargs["query"] == "notice period"
    assert calls.kwargs["limit"] == 6
    assert calls.kwargs["role"] == "hr_manager"
    assert calls.kwargs["company_id"] == COMPANY


@pytest.mark.parametrize(
    "body",
    [
        {"query": ""},
        {"query": "x" * 501},
        {"query": "notice", "limit": 0},
        {"query": "notice", "limit": 7},
        {},
    ],
)
async def test_a_search_outside_the_documented_bounds_is_a_422(
    client: tuple[AsyncClient, FakeSession], monkeypatch: pytest.MonkeyPatch, body: dict[str, Any]
) -> None:
    ac, _db = client
    calls = stub(monkeypatch, "search_corpus", {"semantic": True, "passages": []})

    resp = await ac.post("/hr/library/search", json=body)

    assert resp.status_code == 422
    assert calls.kwargs == {}


# ---------------------------------------------------------------------------
# Refusals — the one shape every screen reads
# ---------------------------------------------------------------------------
async def test_a_refusal_rolls_back_before_it_answers(
    client: tuple[AsyncClient, FakeSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A ``CorpusError`` is raised part-way through a write path (the quota
    check happens after nothing, but ``too_many_chunks`` can fire after several
    inserts), so the session is dirty. Rolling back is what makes
    ``app/corpus.py``'s "caller commits" contract safe: the refusal leaves no
    half-written document behind."""
    ac, db = client
    stub(monkeypatch, "ingest_document", svc.CorpusError(422, "quota_exceeded", "Your library is full."))

    resp = await ac.post(
        "/hr/library",
        data={"title": "Handbook", "audience": "all_staff", "attested": "true"},
        files={"file": ("h.txt", b"text", "text/plain")},
    )

    assert resp.status_code == 422
    assert resp.json()["detail"] == {
        "failure_code": "quota_exceeded", "message": "Your library is full."
    }
    assert db.rollbacks == 1
    assert db.commits == 0


@pytest.mark.parametrize(
    ("name", "method", "path", "status", "code"),
    [
        ("add_version", "post", "/versions", 404, "not_found"),
        ("update_document", "patch", "", 404, "not_found"),
        ("delete_document", "delete", "", 404, "not_found"),
        ("reindex_document", "post", "/reindex", 409, "not_failed"),
        ("download_url", "get", "/download", 410, "gone"),
    ],
)
async def test_every_write_route_reports_a_refusal_in_the_same_shape(
    client: tuple[AsyncClient, FakeSession],
    monkeypatch: pytest.MonkeyPatch,
    name: str, method: str, path: str, status: int, code: str,
) -> None:
    """One body shape across every route, because the frontend has one handler
    for it: a route that answered with a bare string detail would show the user
    a blank error."""
    ac, db = client
    stub(monkeypatch, name, svc.CorpusError(status, code, "Nope."))
    kwargs: dict[str, Any] = {}
    if method == "post" and path == "/versions":
        kwargs["files"] = {"file": ("h.txt", b"text", "text/plain")}
    if method == "patch":
        kwargs["json"] = {"audience": "all_staff"}

    resp = await getattr(ac, method)(f"/hr/library/{DOC}{path}", **kwargs)

    assert resp.status_code == status
    assert resp.json()["detail"] == {"failure_code": code, "message": "Nope."}
    assert db.rollbacks == 1
    assert db.commits == 0
