"""The role-rule matrix for interview_core -- closed by default (2026-10-12).

*Strict, rule-based access. A super admin can SEE things (read-only), but only
the authorised role takes decisions.*

interview_core is the candidate's own interview: sessions, the LiveKit room, the
avatar list and the integrity timeline. It is OWNER-ONLY, not role-based --
every handler compares ``session.user_id`` with the token's ``sub``, and nothing
in the service grants a staff role any extra reach. It takes no hiring decision,
approves nothing, reads no other person's data and manages no user.

The matrix therefore pins three things:

1. every route is listed, and a new one fails until it is classified;
2. no router names a staff role at all (``hr_manager``, ``super_admin`` ...), so
   "staff can see it too" cannot creep in as an inline ``if``;
3. the guest predicate is ONE predicate: any token carrying ``guest_candidate``
   is a guest, with or without another role (changed 2026-10-12 -- it used to
   treat the mixed-role token as an unrestricted account on two paths).
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from shared.auth.jwt import issue_access_token
from shared.testing.route_authz import MUTATING_METHODS, iter_api_routes, route_guards

PUBLIC = "public"
SERVICE = "service"
OWNER = "owner"  # any authenticated account; ownership decided in the handler
OWNER_NO_GUEST = "owner-not-guest"  # ... and never a guest token
OWNER_GUEST_BOUND = "owner-guest-bound"  # ... a guest only for its own bound session

GUARD_KIND: dict[str, str] = {
    "get_current_user": OWNER,
    "get_non_guest_user": OWNER_NO_GUEST,
    "get_guest_bound_user": OWNER_GUEST_BOUND,
    "require_service_jwt": SERVICE,
}
_STRENGTH = [OWNER, OWNER_GUEST_BOUND, OWNER_NO_GUEST, SERVICE]  # weakest -> strongest

MATRIX: dict[tuple[str, str], str] = {
    ("GET", "/"): PUBLIC,
    ("GET", "/metrics"): PUBLIC,
    ("GET", "/health/live"): PUBLIC,
    ("GET", "/health/deep"): SERVICE,
    ("GET", "/api/me"): OWNER,
    ("GET", "/api/avatars"): OWNER,
    ("GET", "/api/sessions"): OWNER,  # a guest is narrowed to its bound session in-handler
    ("POST", "/api/sessions"): OWNER_NO_GUEST,
    ("POST", "/api/rooms/{session_id}/token"): OWNER_GUEST_BOUND,
    # Same dependency as /token: a guest may re-dispatch ONLY its bound session.
    ("POST", "/api/rooms/{session_id}/redispatch"): OWNER_GUEST_BOUND,
    ("POST", "/api/sessions/{session_id}/integrity-events"): OWNER_GUEST_BOUND,
    ("GET", "/api/sessions/{session_id}/integrity"): OWNER_GUEST_BOUND,
}


def _app() -> object:
    from app.main import app

    return app


def _kind(route: object) -> str:
    names = route_guards(route, {}).dep_names
    kinds = [GUARD_KIND[n] for n in names if n in GUARD_KIND]
    if not kinds:
        return PUBLIC
    return max(kinds, key=_STRENGTH.index)


@pytest.fixture(scope="module")
def observed() -> list[tuple[str, str, object, str]]:
    rows = [(m, p, r, _kind(r)) for m, p, r in iter_api_routes(_app())]
    assert len(rows) >= 9, "route discovery failed -- the matrix would pass vacuously"
    return rows


def test_every_route_has_a_matrix_row(observed: list) -> None:
    missing = [f"{m} {p}" for m, p, _r, _k in observed if (m, p) not in MATRIX]
    assert not missing, f"unclassified interview_core routes: {missing}"


def test_no_matrix_row_is_stale(observed: list) -> None:
    live = {(m, p) for m, p, _r, _k in observed}
    assert not (set(MATRIX) - live), sorted(set(MATRIX) - live)


def test_every_route_is_guarded_exactly_as_declared(observed: list) -> None:
    drift = [
        f"{m} {p}: code {k} vs matrix {MATRIX[(m, p)]}"
        for m, p, _r, k in observed
        if (m, p) in MATRIX and k != MATRIX[(m, p)]
    ]
    assert not drift, drift


def test_every_mutation_is_account_bound_never_public_or_staff_granted(observed: list) -> None:
    for m, p, _r, k in observed:
        if m in MUTATING_METHODS:
            assert k in {OWNER_NO_GUEST, OWNER_GUEST_BOUND}, f"{m} {p} is {k}"


def test_session_creation_refuses_every_guest_token(observed: list) -> None:
    kinds = {(m, p): k for m, p, _r, k in observed}
    assert kinds[("POST", "/api/sessions")] == OWNER_NO_GUEST


def test_no_router_grants_a_staff_role_any_extra_reach() -> None:
    """Owner-only means owner-only: no inline staff-role escape hatch."""
    root = Path(__file__).resolve().parents[2] / "app"
    staff = re.compile(r"hr_manager|super_admin|platform_owner|\"admin\"|'admin'")
    offenders = []
    for path in list((root / "routers").glob("*.py")) + [root / "dependencies.py"]:
        text = path.read_text(encoding="utf-8")
        # Strip comments and docstring-ish prose lines that merely mention a role.
        code = "\n".join(
            ln for ln in text.splitlines()
            if not ln.lstrip().startswith(("#", '"', "'"))
        )
        if staff.search(code):
            offenders.append(path.name)
    assert not offenders, (
        "interview_core is owner-only; a staff role appeared in executable code in "
        f"{offenders}. If staff access is intended, add it deliberately -- here and in the matrix."
    )


# ---------------------------------------------------------------------------
# Runtime
# ---------------------------------------------------------------------------
def _tok(roles: list[str], **extra: str) -> str:
    from app.config import settings

    return str(
        issue_access_token(
            user_id=str(uuid.uuid4()),
            roles=roles,
            secret=settings.jwt_secret,
            algorithm=settings.jwt_algorithm,
            extra_claims=dict(extra) or None,
        )
    )


@pytest_asyncio.fixture
async def client():  # type: ignore[no-untyped-def]
    async with AsyncClient(
        transport=ASGITransport(app=_app()), base_url="http://test"  # type: ignore[arg-type]
    ) as ac:
        yield ac


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "roles",
    [["guest_candidate"], ["candidate", "guest_candidate"], ["hr_manager", "guest_candidate"]],
)
async def test_any_guest_token_cannot_create_a_session(
    client: AsyncClient, roles: list[str]
) -> None:
    """One guest predicate, proven over HTTP. The mixed-role cases are the ones
    that used to read as an unrestricted account."""
    resp = await client.post(
        "/api/sessions",
        headers={"Authorization": f"Bearer {_tok(roles)}"},
        json={},
    )
    assert resp.status_code == 403, resp.text
    assert "Guest interview tokens cannot create sessions" in resp.text


@pytest.mark.asyncio
async def test_a_mixed_role_guest_is_still_bound_to_its_own_session(
    client: AsyncClient,
) -> None:
    bound, other = str(uuid.uuid4()), str(uuid.uuid4())
    tok = _tok(["candidate", "guest_candidate"], session_id=bound)
    resp = await client.post(
        f"/api/rooms/{other}/token", headers={"Authorization": f"Bearer {tok}"}, json={}
    )
    assert resp.status_code == 403
    assert "not valid for this session" in resp.text


@pytest.mark.asyncio
async def test_no_token_is_a_401_on_every_guarded_route(
    client: AsyncClient, observed: list
) -> None:
    for m, p, _r, k in observed:
        if k == PUBLIC:
            continue
        resp = await client.request(
            m, re.sub(r"\{[^}]+\}", lambda _m: str(uuid.uuid4()), p),
            json={} if m in MUTATING_METHODS else None,
        )
        assert resp.status_code == 401, f"{m} {p} -> {resp.status_code}"
