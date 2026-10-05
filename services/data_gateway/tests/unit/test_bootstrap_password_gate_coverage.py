"""Every authenticated route either reaches the bootstrap-password gate or is listed.

WHY THIS FILE EXISTS
--------------------
``admin_hr`` provisions every ``hr_manager`` and ``super_admin`` with
``must_change_password = true``, and ``20260625_0002_c3e5f7a9b1d3`` provisions
the platform owner the same way — printing its password to stdout, which is
``docs/ACCEPTED-RISKS.md`` AR-11. ``dependencies.require_password_changed`` is
the server-side control over that flag, and it has exactly one consumer:
``require_role_password_ok``. A route that does not pass through that factory
has no gate, by construction.

Three rounds of review found three different routes in that state, each time by
reading code and each time missing the next one:

* round 13 and 14 — AR-11 claimed the printed password granted no API access.
  It granted ``admin`` across three services.
* round 14 — ``GET /users/{user_id}/profile`` had a role gate and no password
  gate, giving unscoped cross-tenant reads of personal data.
* round 15 — the four ``/agent/*`` routes and ``POST /jobs/{job_id}/jd-document``
  had NO role gate at all, because they decide privilege inside the handler.
  Round 14's sweep had searched for "a role gate with no password gate", a
  predicate that excludes that shape by construction. The cost was concrete: an
  unrotated ``hr_manager`` was refused ``GET /hr/applicants`` and admitted to
  ``POST /agent/panel/{applicant_id}``, which its own comment calls "the
  DENSEST candidate record in the platform".

So this stops asserting it in prose. It walks the LIVE dependency tree of every
registered route and asks the only question that matters — does
``require_password_changed`` appear in it — then requires every authenticated
route that answers no to be on the list below, with a reason. Adding a
privileged route now fails this test until somebody decides which it is.

The list is deliberately by PREFIX and deliberately short. A prefix admits a
whole family, so each one has to be a family that is self-scoped by
construction, not merely harmless today.
"""

from __future__ import annotations

from typing import Any

from fastapi.routing import APIRoute

# Route families that may skip the gate, and why each is sound.
#
# "Self-scoped by construction" means the handler can only ever reach the
# caller's own rows — it resolves the subject from the token, never from a path
# or query parameter. That is what makes the bootstrap flag irrelevant: the
# holder of an unrotated password reaches nothing they do not already own.
_UNGATED_FAMILIES: dict[str, str] = {
    "/auth/": (
        "DELIBERATELY ungated, and named in AR-11. The holder must be able to "
        "reach /auth/me, /auth/change-password and /auth/logout* in order to "
        "CLEAR the flag; gating these would make the flag unclearable. It is "
        "also the reason AR-11 records that whoever reads the printed banner "
        "can take the account over."
    ),
    "/users/me/": (
        "Self-scoped by construction: the subject comes from the token, never "
        "from the path. The caller's own applications, resumes, offers, "
        "onboarding, practice plan and interview loops."
    ),
    "/consent": (
        "The caller's own DPDP consent record, resolved from the token. DPDP "
        "gives the data principal the right to act on their own data; gating "
        "that on a password-rotation chore would be the wrong trade."
    ),
    "/notifications": "The caller's own notifications, resolved from the token.",
    # NOT self-scoped, and the previous version of this entry said it was.
    # `jobs.py`'s own module docstring contradicts it: `GET /jobs/{job_id}`
    # "Returns any active, non-deleted job regardless of owner", and the query
    # filters on id/is_active/deleted_at with no `created_by_user_id` and no
    # company. So the subject comes from a PATH PARAMETER, which is exactly
    # what the definition above excludes.
    #
    # Listed by exact path rather than as a family, because the prefix was
    # excusing routes in other routers (`jd.py` shares it) and because the
    # three paths differ: the browse list returns public rows only, the detail
    # route returns any owner's row, and the create route writes the caller's.
    "/jobs": (
        "Browse list. Returns only public rows (`created_by_user_id IS NULL`), "
        "so there is no caller-specific data to scope."
    ),
    "/jobs/{job_id}": (
        "ACCEPTED, NOT self-scoped: any authenticated caller can read any "
        "active job by UUID, including another user's self-authored practice "
        "job and its pasted JD text. Low sensitivity (a job description, not a "
        "person's record) and a UUIDv4 is the only bound, but recorded as what "
        "it is rather than claimed as scoping that does not exist. Scoping the "
        "query to `created_by_user_id IS NULL OR = caller` is the fix if this "
        "ever matters."
    ),
}


def _matches(prefix: str, path: str) -> bool:
    """Whether *path* is in the family *prefix*, anchored and on a boundary.

    SUBSTRING MATCHING WAS THE BUG, and both round-16 reviewers found it
    independently with working demonstrations. The previous version asked
    `prefix in label`, where `label` was `"GET /hr/applicants"` — so ANY path
    containing `/jobs`, `/consent` or `/notifications` anywhere was excused.
    Demonstrated: `GET /hr/jobs/{company_id}/all-applicants` — authenticated,
    no password gate, privilege decided in-handler, i.e. precisely round 15's
    blocking shape — passed this test, and renaming it to `/hr/roles/...`
    failed it. The only difference was the literal string `/jobs`.

    Worse, it defeated the anti-rot test too: a family can be excusing nothing
    it was written for and still look like it "matches", because some unrelated
    route contains the string. That is the exact condition
    `test_the_listed_families_still_match_something` exists to catch.

    Two routes already sit inside a family this way and are anonymous today, so
    nothing is open: `POST /offer/documents/consent/withdraw` and
    `POST /task/consent/withdraw` are both inside `/consent`. If either ever
    takes a token, the `/consent` entry would have excused it silently.

    Anchored at the start, and on a segment boundary, so `/jobs` admits
    `/jobs` and `/jobs/{job_id}` but never `/hr/jobs/...` or `/jobsearch`.
    """
    if prefix == "/":
        # EXACT ONLY. `"/".rstrip("/") + "/"` is `"/"`, and every path starts
        # with `/`, so treating the root as a prefix made it a blanket pass
        # over the whole app — a prefix blanket replacing the substring
        # blanket this function was written to remove. Caught by the
        # in-handler-authentication mutation, which the root entry was
        # silently excusing.
        return path == "/"
    return path == prefix or path.startswith(prefix.rstrip("/") + "/")


def _in_a_listed_family(path: str) -> bool:
    return any(_matches(prefix, path) for prefix in _UNGATED_FAMILIES)


def _callables(dependant: Any, out: list[Any] | None = None) -> list[Any]:
    """Every callable in a FastAPI dependant tree, including sub-dependencies."""
    if out is None:
        out = []
    if dependant.call is not None:
        out.append(dependant.call)
    for sub in dependant.dependencies:
        _callables(sub, out)
    return out


def _classify() -> tuple[
    list[tuple[str, str]], list[tuple[str, str]], list[tuple[str, str]]
]:
    """(anonymous, password-gated, authenticated-but-ungated) as (methods, path).

    Paths are kept separate from methods so a family can be matched against the
    PATH rather than against a rendered label — see `_matches`.
    """
    from app.dependencies import get_current_user, require_password_changed
    from app.main import app

    anonymous: list[tuple[str, str]] = []
    gated: list[tuple[str, str]] = []
    ungated: list[tuple[str, str]] = []
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        found = _callables(route.dependant)
        methods = ",".join(sorted(route.methods - {"HEAD", "OPTIONS"}))
        entry = (methods, route.path)
        if require_password_changed in found:
            gated.append(entry)
        elif get_current_user in found:
            ungated.append(entry)
        else:
            anonymous.append(entry)
    return anonymous, gated, ungated


def test_every_authenticated_route_is_gated_or_listed() -> None:
    """The claim AR-11 used to make in prose, as a check that cannot go stale."""
    _anonymous, gated, ungated = _classify()

    assert gated, (
        "no route reaches `require_password_changed` at all — this test has "
        "stopped matching, which looks identical to a pass"
    )

    unexplained = [
        f"{methods} {path}"
        for methods, path in ungated
        if not _in_a_listed_family(path)
    ]
    assert not unexplained, (
        "these routes authenticate a caller but never reach "
        "`require_password_changed`, and are not in a listed family:\n    "
        + "\n    ".join(sorted(unexplained))
        + "\n\nAn account provisioned with `must_change_password = true` — every "
        "`hr_manager` and `super_admin` from `admin_hr`, and the platform owner "
        "whose password is PRINTED by its migration (AR-11) — can drive these "
        "before rotating it. Either depend on `require_role_password_ok` (or "
        "`require_password_changed` directly, if the role decision is "
        "in-handler), or add the family to `_UNGATED_FAMILIES` with the reason "
        "it is self-scoped."
    )


def test_the_listed_families_still_match_something() -> None:
    """A prefix that matches nothing is rot, and rot in an allowlist is how a
    real route ends up excused by an entry written for a different one."""
    _anonymous, _gated, ungated = _classify()
    stale = [
        prefix
        for prefix in _UNGATED_FAMILIES
        if not any(_matches(prefix, path) for _methods, path in ungated)
    ]
    assert not stale, (
        f"these exempt families match no ungated route any more: {stale}. "
        "Either they were gated (good — delete the entry) or they were renamed "
        "(in which case this list is now excusing nothing and hiding that)."
    )


def test_the_agent_and_jd_routes_are_gated() -> None:
    """Round 15's finding, pinned by name as well as by the rule above.

    The general rule would catch a regression here anyway. This is here because
    these five are the ones that were actually exploitable — an unrotated
    `hr_manager` refused `GET /hr/applicants` but admitted to the denser read —
    and a named assertion says so to whoever breaks it.
    """
    _anonymous, gated, _ungated = _classify()
    for path in (
        "/agent/chat",
        "/agent/status",
        "/agent/watchers/run",
        "/agent/panel/{applicant_id}",
        "/jobs/{job_id}/jd-document",
    ):
        assert any(p == path for _methods, p in gated), (
            f"{path} no longer reaches the bootstrap-password gate"
        )


# Route families that legitimately take no token at all — ENUMERATED FROM THE
# LIVE APP, not guessed. The first version of this list was written from memory
# and named seven prefixes that match nothing (`/docs`, `/openapi.json`,
# `/redoc` are plain `Route`s and never reach `_classify`; `/consent`,
# `/interview`, `/sso`, `/test-hooks` have no anonymous APIRoute). The anti-rot
# test below caught all seven on the first run, which is the only reason this
# list is now real — and is a small argument for writing the anti-rot half
# before the allowlist.
#
# Every entry is public, or carries its own CAPABILITY token in a header: a long
# random string that IS the authorisation, checked by the handler. None reaches
# `get_current_user`, which is why they land in the anonymous bucket.
_ANONYMOUS_FAMILIES: dict[str, str] = {
    "/": "the service banner. No data.",
    "/apply": (
        "the two anonymous apply doors, their draft token and the activation / "
        "reapply-confirm links. docs/ACCEPTED-RISKS.md AR-10 is entirely about "
        "these, and they are anonymous by product design: a candidate applies "
        "without an account."
    ),
    "/auth": (
        "login, register, verification, password reset, SSO initiate and "
        "callback. Pre-authentication by nature."
    ),
    "/careers": "the public careers board — published openings only.",
    "/exam": "candidate exam links. Capability token in `X-Exam-Token`.",
    "/interview-invite": (
        "invite redemption. The invite code IS the credential, and redeeming "
        "it is how a candidate first gets a session."
    ),
    "/offer": "offer links. Capability token in the header, not a login.",
    "/task": "job-simulation task links. Capability token.",
    "/health": "liveness and deep readiness. No data.",
    "/metrics": (
        "Prometheus. NOT unprotected — `shared/metrics_auth` requires "
        "`Authorization: Bearer $METRICS_TOKEN` and fails CLOSED as a 404 when "
        "`METRICS_TOKEN` is unset under a production APP_ENV, so an "
        "unauthenticated prober cannot tell it from a route that does not "
        "exist. It is in this list because the check is inside the handler "
        "rather than a dependency, which is exactly the shape this test warns "
        "about — named here deliberately rather than discovered later."
    ),
}


def test_the_anonymous_bucket_is_asserted_too() -> None:
    """THE THIRD BUCKET, which both earlier versions silently discarded.

    Round 14's sweep searched for "a role gate with no password gate", and
    round 15's finding was that this excluded routes deciding privilege
    IN-HANDLER. Round 16 pointed out the same shape one level further out:
    `_classify` defines "authenticated" as "reaches `get_current_user`", so a
    route that decodes the token ITSELF — same HS256 secret, checked in the
    handler — lands in `anonymous`, and both tests here threw that list away.

    Not live today: nothing outside `app/dependencies.py` references
    `verify_access_token`, `HTTPBearer` or `Security(`, and every anonymous
    route is public or capability-token. But "not live today" is what was true
    of the in-handler role shape in round 14, one round before it was
    exploitable. So the bucket is enumerated rather than trusted.

    What this cannot see: a route that authenticates in-handler AND sits under
    a listed prefix. That is a real residual limit and it is the reason the
    families below are paths rather than a blanket pass — a new top-level
    prefix shows up here before it can hide anything.
    """
    anonymous, _gated, _ungated = _classify()

    unexplained = [
        f"{methods} {path}"
        for methods, path in anonymous
        if not any(_matches(prefix, path) for prefix in _ANONYMOUS_FAMILIES)
    ]
    assert not unexplained, (
        "these routes take no token and are not in a listed anonymous family:\n    "
        + "\n    ".join(sorted(unexplained))
        + "\n\nEither they are genuinely public or capability-token — in which "
        "case add the family with the reason — or they authenticate INSIDE the "
        "handler, which is the shape round 16 flagged: the password gate, the "
        "role gate and this enumeration would all miss them. A route that "
        "checks its own JWT is not anonymous; it is authenticated somewhere "
        "this walk cannot read."
    )


def test_the_anonymous_families_still_match_something() -> None:
    """Same anti-rot rule as the gated families, for the same reason."""
    anonymous, _gated, _ungated = _classify()
    stale = [
        prefix
        for prefix in _ANONYMOUS_FAMILIES
        if not any(_matches(prefix, path) for _methods, path in anonymous)
    ]
    assert not stale, (
        f"these anonymous families match no route any more: {sorted(stale)}. "
        "Either they were gated (delete the entry) or renamed (in which case "
        "the entry now excuses nothing and hides that)."
    )


def test_no_route_is_served_by_a_private_helper() -> None:
    """A dependency helper must not be the thing a decorator binds to.

    THIS IS A REAL BUG, NOT A STYLE RULE, and it shipped to `main` on
    2026-10-05. The VAPT pass moved the activation token out of the query
    string and into a header — correctly — by adding an `_activation_token`
    dependency, and declared it BETWEEN `@router.get("/activate/target",
    response_model=ActivationTargetOut, ...)` and `read_activation_target`.

    A decorator binds to whatever `def` follows it. So the helper became the
    handler: the route returned a token STRING against a model, every call
    answered 500 with `ResponseValidationError`, and `read_activation_target`
    stopped being registered at all. Candidates could not claim their accounts,
    and the activation link in their email was dead.

    Nothing caught it. The browser specs that fail on it —
    journey-candidate-view, exam-from-dashboard, interview-scheduling — were not
    a CI gate when that merged, and the unit suite does not call the route. It
    surfaced in a service log as a validation error whose `input` was the token.

    The shape generalises past that one function, which is why this is a test
    and not a fixed comparison: an underscore-named handler means a decorator
    captured something never written to be a handler. A deliberate exception
    should be renamed rather than exempted here — the name is the signal.
    """
    from app.main import app  # noqa: PLC0415 — lazily, as `_classify` does

    captured = [
        f"{sorted(route.methods or [])} {route.path} -> {route.endpoint.__name__}"
        for route in app.routes
        if isinstance(route, APIRoute) and route.endpoint.__name__.startswith("_")
    ]
    assert not captured, (
        "these routes are served by private helpers, which means a decorator "
        "bound to the wrong `def` — the function below it rather than the "
        "handler:\n    " + "\n    ".join(captured) + "\n\n"
        "Move the helper ABOVE the decorator. Left alone, the route returns "
        "whatever the helper returns, which is a 500 as soon as the route "
        "declares a response_model, and the real handler is not registered."
    )
