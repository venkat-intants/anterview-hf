"""The role-rule matrix for admin_ops -- closed by default (owner's ruling 2026-10-12).

*Strict, rule-based access. A super admin can SEE things (read-only), but only
the authorised role takes decisions.*

admin_ops has no hiring-decision route at all, and this module keeps it that
way: every route is listed with who may reach it, and the assertions are

* every mounted route has a row (a new route fails until someone classifies it);
* the code's guard is exactly the row's guard;
* every ``/admin/**`` route is a GET reachable only through ``verify_admin_role``
  -- the analytics ``admin`` role, which a company ``super_admin`` does NOT hold;
* the only mutations are the two DPDP erasure routes, and the one that targets
  ANOTHER person is admin-only; the self route refuses a guest token;
* nothing here can advance, hold, reject or hire anyone, whoever calls it;
* at runtime, a real token for every other role is refused with the role gate's
  own message, with no database in reach.

admin_ops authenticates with plain functions (``verify_admin_role`` ...), not the
``require_role`` factory data_gateway uses, so the reach of each guard is
declared here by NAME (``GUARD_ROLES``) and a guard this file has not heard of
fails the test rather than being treated as harmless.
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
ADMIN = frozenset({"admin"})

#: guard name -> roles that pass it. ``verify_account_holder`` refuses a guest
#: INLINE (tested below), so its declared reach is everyone but a guest.
GUARD_ROLES: dict[str, frozenset[str]] = {
    "verify_admin_role": ADMIN,
    "verify_admin_with_roles": ADMIN,
    "verify_account_holder": ALL_ROLES - {"guest_candidate"},
}
KNOWN_UNGUARDED_GUARD_NAMES = {"verify_authenticated_user"}  # would be ANY token; unused today

#: (METHOD, path) -> (roles, kind)
MATRIX: dict[tuple[str, str], tuple[frozenset[str], str]] = {
    ("GET", "/"): (PUBLIC, "PUBLIC"),
    ("GET", "/metrics"): (PUBLIC, "PUBLIC"),
    ("GET", "/health/live"): (PUBLIC, "PUBLIC"),
    ("GET", "/health/deep"): (PUBLIC, "PUBLIC"),
    ("GET", "/admin/status"): (ADMIN, "ADMIN_READ"),
    ("GET", "/admin/overview"): (ADMIN, "ADMIN_READ"),
    ("GET", "/admin/interviews"): (ADMIN, "ADMIN_READ"),
    ("GET", "/admin/interviews/export.csv"): (ADMIN, "ADMIN_READ"),
    ("GET", "/admin/interviews/{session_id}"): (ADMIN, "ADMIN_READ"),
    ("GET", "/admin/interviews/{session_id}/transcript"): (ADMIN, "ADMIN_READ"),
    ("GET", "/admin/analytics/by-role"): (ADMIN, "ADMIN_READ"),
    ("GET", "/admin/analytics/by-language"): (ADMIN, "ADMIN_READ"),
    ("GET", "/admin/analytics/score-distribution"): (ADMIN, "ADMIN_READ"),
    ("GET", "/admin/analytics/trends"): (ADMIN, "ADMIN_READ"),
    ("GET", "/admin/system/health"): (ADMIN, "ADMIN_READ"),
    # DPDP erasure. The "other person" route is admin-only and is narrowed further
    # inline for staff targets (tests/test_erasure_staff_targets.py).
    ("POST", "/users/{user_id}/dpdp/delete"): (ADMIN, "ERASURE_OTHER"),
    ("POST", "/users/me/dpdp/delete"): (ALL_ROLES - {"guest_candidate"}, "ERASURE_SELF"),
}


def _app() -> object:
    from app.main import app

    return app


def _declared_reach(route: object) -> frozenset[str]:
    g = route_guards(route, {})
    names = [n for n in g.dep_names if n in GUARD_ROLES or n in KNOWN_UNGUARDED_GUARD_NAMES]
    if not names:
        return PUBLIC
    reach = ALL_ROLES
    for n in names:
        reach = reach & GUARD_ROLES.get(n, ALL_ROLES)
    return frozenset(reach)


@pytest.fixture(scope="module")
def observed() -> list[tuple[str, str, object, frozenset[str]]]:
    rows = [(m, p, r, _declared_reach(r)) for m, p, r in iter_api_routes(_app())]
    assert len(rows) >= 15, "route discovery failed -- the matrix would pass vacuously"
    return rows


def test_every_route_has_a_matrix_row(observed: list) -> None:
    missing = [f"{m} {p}" for m, p, _r, _e in observed if (m, p) not in MATRIX]
    assert not missing, f"unclassified admin_ops routes: {missing}"


def test_no_matrix_row_is_stale(observed: list) -> None:
    live = {(m, p) for m, p, _r, _e in observed}
    assert not (set(MATRIX) - live), f"rows with no live route: {sorted(set(MATRIX) - live)}"


def test_every_route_is_reachable_by_exactly_the_declared_roles(observed: list) -> None:
    drift = [
        f"{m} {p}: code {sorted(eff)} vs matrix {sorted(MATRIX[(m, p)][0])}"
        for m, p, _r, eff in observed
        if (m, p) in MATRIX and eff != MATRIX[(m, p)][0]
    ]
    assert not drift, drift


def test_an_unrecognised_guard_cannot_hide_a_route(observed: list) -> None:
    """A route guarded by something this file has no name for would read as PUBLIC
    and mismatch its row -- and a route whose row says PUBLIC but whose code now
    carries a guard also mismatches. Either way a human decides, not a default."""
    for m, p, _r, eff in observed:
        if MATRIX.get((m, p), (None, ""))[1] == "PUBLIC":
            assert eff == PUBLIC


def test_the_admin_surface_is_read_only_and_admin_only(observed: list) -> None:
    admin_routes = [(m, p, e) for m, p, _r, e in observed if p.startswith("/admin/")]
    assert len(admin_routes) >= 10
    for m, p, eff in admin_routes:
        assert m == "GET", f"{m} {p}: the admin console is read-only"
        assert eff == ADMIN, f"{m} {p} reachable by {sorted(eff)}"


def test_the_only_mutations_are_the_two_erasure_routes(observed: list) -> None:
    mutating = {(m, p) for m, p, _r, _e in observed if m in MUTATING_METHODS}
    assert mutating == {
        ("POST", "/users/{user_id}/dpdp/delete"),
        ("POST", "/users/me/dpdp/delete"),
    }


def test_no_route_can_decide_a_candidate(observed: list) -> None:
    decisive = re.compile(r"decision|status|advance|reject|hire|shortlist|enrolment|offer")
    for m, p, _r, _e in observed:
        if m in MUTATING_METHODS:
            assert not decisive.search(p), f"{m} {p} looks like a hiring action in admin_ops"


def test_roles_that_only_view_or_apply_reach_nothing_privileged(observed: list) -> None:
    for m, p, _r, eff in observed:
        kind = MATRIX.get((m, p), (None, ""))[1]
        if kind in {"ADMIN_READ", "ERASURE_OTHER"}:
            assert not (eff & {"super_admin", "hr_manager", "interviewer", "candidate",
                               "guest_candidate", "service"}), f"{m} {p}"


# ---------------------------------------------------------------------------
# Runtime
# ---------------------------------------------------------------------------
def _token(role: str) -> str:
    from app.config import settings

    return str(
        issue_access_token(
            user_id=str(uuid.uuid4()),
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
async def test_a_real_token_without_the_admin_role_is_refused_by_the_role_gate(
    client: AsyncClient, observed: list
) -> None:
    pairs, failures = 0, []
    for m, p, _r, eff in observed:
        if MATRIX.get((m, p), (None, ""))[1] not in {"ADMIN_READ", "ERASURE_OTHER"}:
            continue
        for role in sorted(ALL_ROLES - eff):
            pairs += 1
            resp = await client.request(
                m, _fill(p), headers={"Authorization": f"Bearer {_token(role)}"},
                json={} if m in MUTATING_METHODS else None,
            )
            if resp.status_code != 403 or "Admin role required" not in resp.text:
                failures.append(f"{role} {m} {p} -> {resp.status_code} {resp.text[:60]}")
    assert pairs >= 70
    assert not failures, "\n".join(failures[:15])


@pytest.mark.asyncio
async def test_a_guest_token_cannot_erase_an_account_through_the_self_route(
    client: AsyncClient,
) -> None:
    resp = await client.post(
        "/users/me/dpdp/delete", headers={"Authorization": f"Bearer {_token('guest_candidate')}"},
        json={},
    )
    assert resp.status_code == 403
    assert "signing in to your account" in resp.text


@pytest.mark.asyncio
async def test_no_token_at_all_is_a_401_on_every_guarded_route(
    client: AsyncClient, observed: list
) -> None:
    for m, p, _r, eff in observed:
        if eff == PUBLIC:
            continue
        resp = await client.request(
            m, _fill(p), json={} if m in MUTATING_METHODS else None
        )
        assert resp.status_code == 401, f"{m} {p} -> {resp.status_code}"
