"""Read a FastAPI app's authorisation out of its resolved dependency trees.

The role-rule matrix tests (``tests/unit/test_role_rule_matrix.py`` in each
service) need to answer, for every mounted route, "which roles can reach this?"
without a database and without trusting where a guard was *meant* to be wired.
FastAPI flattens router-level, include-level and route-level ``dependencies``
into each route's ``dependant`` tree at include time, so walking that tree sees
the guard wherever it was declared -- the same approach
``test_candidate_route_authz.py`` and ``ops/ci/check_routing_contract.py`` take.

Two guard shapes are understood:

* ``require_role(*allowed)`` -- every such dependency on a route must pass, so
  the reachable set is the INTERSECTION of the ``allowed`` tuples;
* ``reject_role(*denied)`` -- subtracts roles.

Both factories close over their tuple, so it is read from the function's free
variables BY NAME (``allowed`` / ``denied``) rather than by shape. Anything else
(``get_current_user``, a link-token context, ``verify_admin_role``...) is
recognised by the caller-supplied ``auth_markers`` mapping, and a route that
matches neither is reported as ``anonymous`` -- which a matrix must then name
explicitly, so an unguarded route can never pass by omission.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any

#: Every role the platform issues, plus the two token types that are not accounts.
ALL_ROLES: frozenset[str] = frozenset(
    {
        "guest_candidate",
        "candidate",
        "interviewer",
        "hr_manager",
        "super_admin",
        "admin",
        "platform_owner",
        "service",
    }
)

MUTATING_METHODS: frozenset[str] = frozenset({"POST", "PUT", "PATCH", "DELETE"})


@dataclass(frozen=True)
class Guards:
    """What a route's dependency tree enforces."""

    require: tuple[frozenset[str], ...] = ()
    reject: frozenset[str] = frozenset()
    # Names from the caller's ``auth_markers`` that appear in the tree.
    markers: frozenset[str] = frozenset()
    # Every dependency callable's name -- for assertions about which helper a
    # route goes through (e.g. "get_hr_company").
    dep_names: frozenset[str] = frozenset()
    extras: dict[str, Any] = field(default_factory=dict, compare=False)

    @property
    def authenticated(self) -> bool:
        return bool(self.require or self.reject or self.markers)


def _freevar(fn: Any, name: str) -> Any | None:
    code = getattr(fn, "__code__", None)
    closure = getattr(fn, "__closure__", None)
    if code is None or not closure:
        return None
    for var, cell in zip(code.co_freevars, closure, strict=False):
        if var == name:
            try:
                return cell.cell_contents
            except ValueError:  # empty cell
                return None
    return None


def route_guards(route: Any, auth_markers: Mapping[str, str] | None = None) -> Guards:
    """Resolve ``route``'s guards. ``auth_markers`` maps dependency NAME -> label."""
    markers = dict(auth_markers or {})
    require: list[frozenset[str]] = []
    reject: set[str] = set()
    found: set[str] = set()
    names: set[str] = set()

    root = getattr(route, "dependant", None)
    stack = list(getattr(root, "dependencies", []) or [])
    seen: set[int] = set()
    while stack:
        node = stack.pop()
        if node is None or id(node) in seen:
            continue
        seen.add(id(node))
        fn = getattr(node, "call", None)
        qual = getattr(fn, "__qualname__", "") or ""
        name = getattr(fn, "__name__", "") or ""
        if name:
            names.add(name)
        if qual.startswith("require_role."):
            allowed = _freevar(fn, "allowed")
            if isinstance(allowed, tuple):
                require.append(frozenset(str(a) for a in allowed))
        elif qual.startswith("reject_role."):
            denied = _freevar(fn, "denied")
            if isinstance(denied, tuple):
                reject |= {str(d) for d in denied}
        if name in markers:
            found.add(markers[name])
        stack.extend(getattr(node, "dependencies", []) or [])

    return Guards(
        require=tuple(require),
        reject=frozenset(reject),
        markers=frozenset(found),
        dep_names=frozenset(names),
    )


def can_reach(g: Guards, role: str) -> bool:
    """Would a token holding ONLY ``role`` pass every role gate on the route?"""
    if any(role not in allowed for allowed in g.require):
        return False
    return role not in g.reject


def reachable_roles(g: Guards, universe: Iterable[str] = ALL_ROLES) -> frozenset[str]:
    return frozenset(r for r in universe if can_reach(g, r))


def iter_api_routes(app: Any) -> Iterator[tuple[str, str, Any]]:
    """Yield ``(METHOD, path, route)`` for every mounted HTTP route.

    One tuple per method; HEAD/OPTIONS are framework noise and skipped. Mounts,
    websockets and the OpenAPI/docs routes carry no ``dependant`` and are skipped
    -- a mounted sub-application would need its own entry, which is why the
    callers also assert the route count is non-zero.
    """
    for route in app.routes:
        if not hasattr(route, "dependant"):
            continue
        for method in sorted(getattr(route, "methods", None) or ()):
            if method in {"HEAD", "OPTIONS"}:
                continue
            yield method, route.path, route


@dataclass(frozen=True)
class Rule:
    """One matrix row.

    ``path`` is either an exact route template (``/hr/offers/{offer_id}/send``)
    or a prefix family ending in ``/**``. Exact rows win over families. ``methods``
    of ``None`` means every method. ``roles`` is the set of ROLES that may reach
    the route; the string ``"*"`` stands for "no authentication" (public or
    token-in-body).
    """

    methods: frozenset[str] | None
    path: str
    roles: frozenset[str]
    kind: str

    def matches(self, method: str, path: str) -> bool:
        if self.methods is not None and method not in self.methods:
            return False
        if self.path.endswith("/**"):
            return path == self.path[:-3] or path.startswith(self.path[:-2])
        return path == self.path


def find_rule(rules: Iterable[Rule], method: str, path: str) -> Rule | None:
    """Exact rows first, then the first matching family."""
    fallback: Rule | None = None
    for rule in rules:
        if not rule.matches(method, path):
            continue
        if not rule.path.endswith("/**"):
            return rule
        if fallback is None:
            fallback = rule
    return fallback


def effective_roles(g: Guards, universe: Iterable[str] = ALL_ROLES) -> frozenset[str]:
    """Roles reaching the route.

    ``{"*"}`` when no ACCOUNT authenticates it: either nothing does (public), or
    only a link/body token does (marker label ``"link"``) -- neither is a role.
    A route behind ``get_current_user`` alone (marker ``"authed"``) is reachable
    by every role that can hold a token, ``service`` and ``guest_candidate``
    included, which is exactly what a matrix row must then say.
    """
    if g.require or g.reject or "authed" in g.markers:
        return reachable_roles(g, universe)
    return frozenset({"*"})
