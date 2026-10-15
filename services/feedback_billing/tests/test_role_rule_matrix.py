"""The role-rule matrix for feedback_billing -- closed by default (2026-10-12).

*Strict, rule-based access. A super admin can SEE things (read-only), but only
the authorised role takes decisions.*

feedback_billing has no human-facing write at all. Its surface is:

* ``GET /api/scorecards*`` -- an authenticated reader; WHICH scorecards is decided
  inline by ownership / same-company scope (``routers/scorecard.py``), pinned
  below, with a guest bound to the one session its token names;
* ``POST /internal/*`` -- the service-to-service surface (scoring, resume
  scoring, exam generation, embeddings, PDF rendering): the ``service`` role AND
  a pinned service subject. No human role of any kind reaches it.

So the matrix here is short, and its job is to keep it short: a new route fails
until it is classified, a human-reachable write fails outright, and the
read-privilege set for other people's scorecards is pinned so widening it is a
visible, reviewed edit.

Declared deliberately, not changed: ``hr_manager``, ``super_admin``, ``admin``
and ``platform_owner`` may READ a same-company candidate's scorecard
(``_PRIVILEGED_ROLES``). A company ``super_admin`` therefore has read access to
named interview scorecards at the REST layer -- consistent with the owner's
"can see, cannot act" ruling and recorded in docs/ACCEPTED-RISKS.md (AR-10). It
can do nothing else here, which is what the tests below prove.
"""

from __future__ import annotations

import re
import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from shared.auth.jwt import issue_access_token
from shared.testing.route_authz import ALL_ROLES, MUTATING_METHODS, iter_api_routes, route_guards

PUBLIC = frozenset({"*"})
SERVICE = frozenset({"service"})
ANY_TOKEN = ALL_ROLES  # require_jwt: authenticated; scope decided inline

GUARD_ROLES: dict[str, frozenset[str]] = {
    "require_jwt": ANY_TOKEN,
    "_require_jwt": ANY_TOKEN,  # alias kept in score.py for old patch targets
    "_require_service_jwt": SERVICE,
}

MATRIX: dict[tuple[str, str], tuple[frozenset[str], str]] = {
    ("GET", "/"): (PUBLIC, "PUBLIC"),
    ("GET", "/metrics"): (PUBLIC, "PUBLIC"),
    ("GET", "/health/live"): (PUBLIC, "PUBLIC"),
    ("GET", "/health/deep"): (PUBLIC, "PUBLIC"),
    ("GET", "/api/scorecards"): (ANY_TOKEN, "OWN_READ"),
    ("GET", "/api/scorecards/{scorecard_id}"): (ANY_TOKEN, "SCOPED_READ"),
    ("POST", "/internal/score"): (SERVICE, "INTERNAL"),
    ("POST", "/internal/score-resume"): (SERVICE, "INTERNAL"),
    ("POST", "/internal/generate-exam"): (SERVICE, "INTERNAL"),
    ("POST", "/internal/generate-coding"): (SERVICE, "INTERNAL"),
    ("POST", "/internal/embed"): (SERVICE, "INTERNAL"),
    ("POST", "/internal/why-match"): (SERVICE, "INTERNAL"),
    ("POST", "/internal/scorecards/{scorecard_id}/pdf"): (SERVICE, "INTERNAL"),
}


def _app() -> object:
    from app.main import app

    return app


def _declared_reach(route: object) -> frozenset[str]:
    names = [n for n in route_guards(route, {}).dep_names if n in GUARD_ROLES]
    if not names:
        return PUBLIC
    reach = ALL_ROLES
    for n in names:
        reach = reach & GUARD_ROLES[n]
    return frozenset(reach)


@pytest.fixture(scope="module")
def observed() -> list[tuple[str, str, object, frozenset[str]]]:
    rows = [(m, p, r, _declared_reach(r)) for m, p, r in iter_api_routes(_app())]
    assert len(rows) >= 10, "route discovery failed -- the matrix would pass vacuously"
    return rows


def test_every_route_has_a_matrix_row(observed: list) -> None:
    missing = [f"{m} {p}" for m, p, _r, _e in observed if (m, p) not in MATRIX]
    assert not missing, f"unclassified feedback_billing routes: {missing}"


def test_no_matrix_row_is_stale(observed: list) -> None:
    live = {(m, p) for m, p, _r, _e in observed}
    assert not (set(MATRIX) - live), sorted(set(MATRIX) - live)


def test_every_route_is_guarded_exactly_as_declared(observed: list) -> None:
    drift = [
        f"{m} {p}: code {sorted(eff)} vs matrix {sorted(MATRIX[(m, p)][0])}"
        for m, p, _r, eff in observed
        if (m, p) in MATRIX and eff != MATRIX[(m, p)][0]
    ]
    assert not drift, drift


def test_no_human_role_can_write_anything(observed: list) -> None:
    """The only mutations are /internal/*, reachable by the service role alone."""
    for m, p, _r, eff in observed:
        if m in MUTATING_METHODS:
            assert p.startswith("/internal/"), f"{m} {p}: a human-facing write appeared"
            assert eff == SERVICE, f"{m} {p} reachable by {sorted(eff)}"
            assert not (eff & {"super_admin", "hr_manager", "admin", "platform_owner",
                               "interviewer", "candidate", "guest_candidate"})


def test_the_privileged_scorecard_readers_are_pinned() -> None:
    """Widening who may read OTHER people's scorecards is a deliberate edit.

    ``interviewer`` and ``candidate`` are absent on purpose: a candidate reads
    their own through the ownership branch, and nobody else gets in by role.
    """
    from app.routers.scorecard import _PRIVILEGED_ROLES

    assert frozenset(
        {"hr_manager", "super_admin", "admin", "platform_owner"}
    ) == _PRIVILEGED_ROLES


def test_guest_tokens_are_recognised_by_any_guest_marker() -> None:
    """One guest predicate: the role OR a session binding, alongside any other role."""
    from app.auth import is_guest_token

    assert is_guest_token({"roles": ["guest_candidate"]})
    assert is_guest_token({"roles": ["guest_candidate", "candidate"]})
    assert is_guest_token({"roles": ["hr_manager", "guest_candidate"]})
    assert is_guest_token({"roles": ["candidate"], "session_id": "abc"})
    assert not is_guest_token({"roles": ["candidate"]})


# ---------------------------------------------------------------------------
# Runtime
# ---------------------------------------------------------------------------
def _token(role: str, sub: str | None = None) -> str:
    from app.config import settings

    return str(
        issue_access_token(
            user_id=sub or str(uuid.uuid4()),
            roles=[role],
            secret=settings.jwt_secret,
            algorithm=settings.jwt_algorithm,
            issuer=settings.jwt_issuer,
            audience=settings.jwt_audience,
        )
    )


def _fill(path: str) -> str:
    return re.sub(r"\{[^}]+\}", lambda _m: str(uuid.uuid4()), path)


@pytest_asyncio.fixture
async def client():  # type: ignore[no-untyped-def]
    async with AsyncClient(
        transport=ASGITransport(app=_app()), base_url="http://test"  # type: ignore[arg-type]
    ) as ac:
        yield ac


@pytest.mark.asyncio
async def test_every_human_role_is_refused_on_the_service_surface(
    client: AsyncClient, observed: list
) -> None:
    pairs, failures = 0, []
    for m, p, _r, _e in observed:
        if MATRIX.get((m, p), (None, ""))[1] != "INTERNAL":
            continue
        for role in sorted(ALL_ROLES - SERVICE):
            # Once with a user UUID subject, once WITH a pinned service subject: the
            # second isolates the ROLE check from the subject allowlist, so deleting
            # either one alone is caught.
            for sub in (None, "interview_core"):
                pairs += 1
                resp = await client.request(
                    m, _fill(p),
                    headers={"Authorization": f"Bearer {_token(role, sub)}"}, json={},
                )
                if resp.status_code != 403:
                    failures.append(f"{role}/{sub} {m} {p} -> {resp.status_code}")
    assert pairs >= 80
    assert not failures, "\n".join(failures[:15])


@pytest.mark.asyncio
async def test_a_service_role_on_a_user_uuid_subject_is_still_refused(
    client: AsyncClient, observed: list
) -> None:
    """The role alone is not enough: the subject must be a pinned service name."""
    for m, p, _r, _e in observed:
        if MATRIX.get((m, p), (None, ""))[1] == "INTERNAL":
            resp = await client.request(
                m, _fill(p), headers={"Authorization": f"Bearer {_token('service')}"}, json={}
            )
            assert resp.status_code == 403, f"{m} {p} -> {resp.status_code}"


@pytest.mark.asyncio
async def test_no_token_is_a_401_on_every_guarded_route(
    client: AsyncClient, observed: list
) -> None:
    for m, p, _r, eff in observed:
        if eff == PUBLIC:
            continue
        resp = await client.request(m, _fill(p), json={} if m in MUTATING_METHODS else None)
        assert resp.status_code == 401, f"{m} {p} -> {resp.status_code}"


@pytest.mark.asyncio
async def test_a_guest_cannot_list_scorecards_even_with_another_role(
    client: AsyncClient,
) -> None:
    """Runtime proof of the single guest predicate on the list route."""
    from unittest.mock import MagicMock

    from app.config import settings
    from app.database import get_db_session

    async def _no_db():  # type: ignore[no-untyped-def]
        yield MagicMock()  # the guest check refuses before any query is run

    app = _app()
    app.dependency_overrides[get_db_session] = _no_db  # type: ignore[attr-defined]
    try:
        await _guest_list_is_refused(client, settings)
    finally:
        app.dependency_overrides.pop(get_db_session, None)  # type: ignore[attr-defined]


async def _guest_list_is_refused(client: AsyncClient, settings: object) -> None:
    for roles in (["guest_candidate"], ["candidate", "guest_candidate"]):
        tok = issue_access_token(
            user_id=str(uuid.uuid4()),
            roles=roles,
            secret=settings.jwt_secret,  # type: ignore[attr-defined]
            algorithm=settings.jwt_algorithm,  # type: ignore[attr-defined]
            issuer=settings.jwt_issuer,  # type: ignore[attr-defined]
            audience=settings.jwt_audience,  # type: ignore[attr-defined]
        )
        resp = await client.get("/api/scorecards", headers={"Authorization": f"Bearer {tok}"})
        assert resp.status_code == 403, (roles, resp.status_code)
