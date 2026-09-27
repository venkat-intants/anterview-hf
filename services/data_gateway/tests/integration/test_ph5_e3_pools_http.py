"""PH5-E3 HTTP-level tests — the pools and rediscovery routes' ROLE GATE, end
to end through the real ASGI app and real JWTs.

WHY THIS FILE EXISTS. The independent acceptance pass marked criteria 5
("candidate permissions and access controls are enforced during rediscovery")
and 19 ("tests cover permissions, …") as ⚠️ for one reason: every other
property of these two routers was proved somewhere, but NOTHING ever called
them over HTTP as a ``super_admin``, an ``interviewer``, a ``candidate`` or a
``guest_candidate`` to watch a live 403. The service layer was tested directly
and the frontend's nav scoping was tested in Vitest, which leaves the actual
wire behaviour asserted only by "it is the same dependency every other /hr
router uses". True, and still not evidence. The design's §9 asked for this
file; it was never written.

The distinction these routes turn on is the whole point of criterion 5, so it
is worth stating plainly: a company ``super_admin`` shares every route in the
document library but is refused here. A pool and a rediscovery result both
name a candidate, ``DATA_CLASS_ROLES["candidate_pii"]`` is ``{hr_manager}``,
and CLAUDE.md says a company super admin is deliberately NOT a superset of HR.
So this router is narrower than ``hr_corpus.py`` ON PURPOSE, and a future
change that "tidied up" the two to match would be a disclosure, not a
simplification. That is what these tests stand guard over.

WHY EVERY ROLE GETS A SEEDED USER ROW, which looks like more setup than a role
check needs. There are TWO different 403s behind ``HrCtxDep``: one from
``require_role_password_ok("hr_manager")`` (wrong role) and one from
``get_hr_company`` (right role, but "your account is not assigned to a
company" — ``dependencies.py:280-284``). A first draft of this file issued
bare tokens with no user rows at all, and every role returned 403 — including
``hr_manager``. It passed, and it would have passed just as happily with the
role gate deleted entirely, because the refusals were all coming from the
company check. That is precisely the class of test this project has shipped
five times already and it is worth the extra fixture to avoid a sixth.

So: every role here is a real user of a real company, differing ONLY in its
roles claim, and ``test_an_hr_manager_is_not_refused_on_the_same_routes`` is
the positive control that keeps the negatives meaningful. If the gate were
removed, that control would still pass — but the seven negative cases would
fail, which is the direction that matters.

On the ``test_ph5_e2_corpus_http.py`` precedent: an in-process
``httpx.AsyncClient`` over ``ASGITransport`` running the app's own lifespan,
against this repo's disposable Postgres.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from shared.auth.jwt import issue_access_token
from shared.db.engine import build_engine, build_session_factory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings

pytestmark = pytest.mark.integration

#: Every role that is NOT ``hr_manager``. ``super_admin`` is the one that
#: matters most — it is the near miss, and the only one a reader might expect
#: to be allowed.
_REFUSED_ROLES: tuple[str, ...] = (
    "super_admin", "platform_owner", "admin", "interviewer", "candidate", "guest_candidate",
)


@pytest_asyncio.fixture
async def committed_db() -> AsyncIterator[AsyncSession]:
    """A session that COMMITS — the ASGI app reads through its own connection
    and cannot see an uncommitted write on this one (the
    ``test_ph5_e2_corpus_http.py`` precedent)."""
    engine = build_engine(
        database_url=settings.database_url, database_ssl=settings.database_ssl, pool_size=2,
    )
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


@pytest_asyncio.fixture
async def staff(committed_db: AsyncSession) -> AsyncIterator[dict[str, uuid.UUID]]:
    """One company, and one user row per role — all WITH a company, so the only
    thing that differs between them is the roles claim on the token."""
    company_id = uuid.uuid4()
    tag = company_id.hex[:10]
    ids = {role: uuid.uuid4() for role in (*_REFUSED_ROLES, "hr_manager")}
    await committed_db.execute(
        text("INSERT INTO companies (id, name, slug) VALUES (:c, 'HTTP pools co', :s)"),
        {"c": company_id, "s": f"pools-http-{tag}"},
    )
    for role, uid in ids.items():
        await committed_db.execute(
            text("INSERT INTO users (id, email, company_id) VALUES (:u, :e, :c)"),
            {"u": uid, "e": f"{role}-{tag}@pools.test", "c": company_id},
        )
    await committed_db.commit()
    try:
        yield {**ids, "company": company_id}
    finally:
        # `companies` cascades to the pool tables; `users.company_id` is only
        # ON DELETE SET NULL, so the user rows go explicitly by id.
        await committed_db.execute(
            text("DELETE FROM companies WHERE id = :c"), {"c": company_id},
        )
        await committed_db.execute(
            text("DELETE FROM users WHERE id = ANY(:u)"), {"u": list(ids.values())},
        )
        await committed_db.commit()


def _auth(user_id: uuid.UUID, roles: list[str]) -> dict[str, str]:
    token = issue_access_token(
        str(user_id), roles, settings.jwt_secret, issuer=settings.jwt_issuer,
        audience=settings.jwt_audience,
    )
    return {"Authorization": f"Bearer {token}"}


#: Every route these two routers expose. A pool id that belongs to nobody is
#: correct for the role cases: the gate must refuse BEFORE the id is looked up,
#: so a 404 here would itself be the finding — it would make the route an
#: existence oracle for another tenant's pool ids.
_POOL = uuid.uuid4()
_MEMBER = uuid.uuid4()
_ROUTES: tuple[tuple[str, str, dict[str, object] | None], ...] = (
    ("GET", "/hr/pools", None),
    ("POST", "/hr/pools", {"name": "Fitters"}),
    ("GET", f"/hr/pools/{_POOL}", None),
    ("PATCH", f"/hr/pools/{_POOL}", {"name": "Renamed"}),
    ("DELETE", f"/hr/pools/{_POOL}", None),
    ("POST", f"/hr/pools/{_POOL}/members",
     {"applicant_ids": [str(uuid.uuid4())], "source": "manual"}),
    ("DELETE", f"/hr/pools/{_POOL}/members/{_MEMBER}", None),
    ("POST", f"/hr/pools/{_POOL}/members/{_MEMBER}/evidence-reviewed", None),
    ("POST", f"/hr/pools/{_POOL}/members/{_MEMBER}/invite",
     {"requisition_id": str(uuid.uuid4())}),
    ("POST", "/hr/rediscovery/search", {"query": "fitter"}),
    ("GET", "/hr/rediscovery/universe", None),
)


async def _call(client: AsyncClient, method: str, path: str,
                body: dict[str, object] | None, headers: dict[str, str]) -> int:
    if method == "GET":
        return (await client.get(path, headers=headers)).status_code
    if method == "DELETE":
        # Two of these routes carry an optional JSON body on DELETE.
        return (await client.request("DELETE", path, headers=headers, json=body)).status_code
    if method == "PATCH":
        return (await client.patch(path, headers=headers, json=body)).status_code
    return (await client.post(path, headers=headers, json=body or {})).status_code


@pytest.mark.asyncio
@pytest.mark.parametrize("role", _REFUSED_ROLES)
async def test_every_role_but_hr_manager_is_refused_on_every_route(
    client: AsyncClient, staff: dict[str, uuid.UUID], role: str,
) -> None:
    """403 is the only acceptable answer, on every route, for every role that
    is not ``hr_manager`` — and each of these accounts DOES belong to a
    company, so the refusal can only be the role gate."""
    headers = _auth(staff[role], [role])
    for method, path, body in _ROUTES:
        status = await _call(client, method, path, body, headers)
        assert status == 403, f"{role} got {status} from {method} {path}, expected 403"


@pytest.mark.asyncio
async def test_an_hr_manager_is_not_refused_on_the_same_routes(
    client: AsyncClient, staff: dict[str, uuid.UUID],
) -> None:
    """The positive control, without which the negatives above prove nothing.

    An ``hr_manager`` of the same company must get PAST the gate. What it then
    gets is a 404 (the pool ids above belong to nobody) or a 200 on the two
    collection routes — never 403. The first draft of this file had no such
    control and would have passed with the role gate deleted.
    """
    headers = _auth(staff["hr_manager"], ["hr_manager"])
    for method, path, body in _ROUTES:
        status = await _call(client, method, path, body, headers)
        assert status != 403, (
            f"hr_manager was refused {method} {path} — the negative cases in this "
            f"file therefore prove nothing about the ROLE gate"
        )


@pytest.mark.asyncio
async def test_no_token_at_all_is_refused_on_every_route(client: AsyncClient) -> None:
    for method, path, body in _ROUTES:
        status = await _call(client, method, path, body, {})
        assert status in (401, 403), f"anonymous got {status} from {method} {path}"


@pytest.mark.asyncio
async def test_the_route_table_here_covers_every_route_the_two_routers_mount() -> None:
    """A guard on the guard: if someone adds a route to either router and does
    not list it above, every test in this file would still pass while saying
    nothing at all about the new route.
    """
    from app.main import app

    mounted: set[tuple[str, str]] = set()
    for route in app.routes:
        path = getattr(route, "path", "")
        if not (path.startswith("/hr/pools") or path.startswith("/hr/rediscovery")):
            continue
        for method in getattr(route, "methods", set()) or set():
            if method in {"HEAD", "OPTIONS"}:
                continue
            mounted.add((method, path))

    assert mounted, "the scan found no pools/rediscovery routes — has this gone blind?"

    covered = {
        (method, path.replace(str(_POOL), "{pool_id}").replace(str(_MEMBER), "{member_id}"))
        for method, path, _body in _ROUTES
    }
    assert mounted == covered, (
        f"route table out of date.\n  mounted but untested: {sorted(mounted - covered)}"
        f"\n  tested but not mounted: {sorted(covered - mounted)}"
    )
