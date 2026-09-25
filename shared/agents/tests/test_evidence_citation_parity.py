"""PH5-E5 — ``CitationKind`` must name exactly the same members as the
TypeScript ``Citation.kind`` union in ``web/src/api/agent.ts``.

Nothing enforces that agreement at the type level (one is a Python Literal,
the other a TS union in a different package), so a member added to one and
not the other would only ever surface as a citation the frontend renders with
no icon or label. This test parses the TS union out of the source file and
diffs it against ``typing.get_args(CitationKind)``.

PH5-E1 adds two more tables keyed by the same closed vocabulary —
``CITATION_ROUTES`` (mirrored in TS, since the frontend needs the href
template too) and ``CITATION_MIN_ROLES`` (server-only: nothing in the
frontend enforces access, so there is no TS side to parse). Both are extended
here rather than in a new file, since "does this table cover every kind" is
the same question the file already asks about ``CitationKind`` itself.

Regex fragility, noted rather than silently relied on (PH5 Wave 3 design §9
Q15): ``_ts_citation_kinds`` greeds up to the FIRST ``;`` after ``kind:``
inside the FIRST ``export interface Citation {`` block it finds. A field
literally named ``kind`` declared above ``Citation`` in this file, or a second
``interface Citation`` block, would silently change what this parses without
failing loudly. It is a source-scan, not a compiler, and is only as good as
the file staying in the shape it is in today.
"""

from __future__ import annotations

import pathlib
import re
import typing

from shared.agents.schema import CITATION_MIN_ROLES, CITATION_ROUTES, CitationKind

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
AGENT_TS = REPO_ROOT / "web" / "src" / "api" / "agent.ts"


def _ts_citation_kinds() -> set[str]:
    src = AGENT_TS.read_text(encoding="utf-8")
    # The first `kind:` union in the file is Citation's — grab the block up to
    # the closing `;` and pull every single-quoted literal out of it.
    match = re.search(r"export interface Citation \{.*?kind:\s*(.*?);", src, re.DOTALL)
    assert match is not None, "Citation.kind union not found in agent.ts"
    return set(re.findall(r"'([a-z_]+)'", match.group(1)))


def _ts_citation_routes() -> dict[str, str | None]:
    src = AGENT_TS.read_text(encoding="utf-8")
    match = re.search(
        r"export const CITATION_ROUTES:.*?=\s*\{(.*?)\};", src, re.DOTALL
    )
    assert match is not None, "CITATION_ROUTES not found in agent.ts"
    body = match.group(1)
    routes: dict[str, str | None] = {}
    for key, value in re.findall(r"(\w+):\s*(null|'[^']*')", body):
        routes[key] = None if value == "null" else value.strip("'")
    return routes


def test_citation_kind_matches_the_typescript_union() -> None:
    python_kinds = set(typing.get_args(CitationKind))
    ts_kinds = _ts_citation_kinds()
    assert python_kinds == ts_kinds, (
        f"CitationKind (Python) and Citation.kind (TS) have drifted: "
        f"python only={python_kinds - ts_kinds}, ts only={ts_kinds - python_kinds}"
    )


def test_evidence_graph_citation_kinds_are_declared() -> None:
    """The two PH5-E5 additions are present on both sides, by name."""
    python_kinds = set(typing.get_args(CitationKind))
    assert {"interviewer_scorecard", "decision"} <= python_kinds
    assert {"interviewer_scorecard", "decision"} <= _ts_citation_kinds()


def test_document_citation_kind_is_declared_on_both_sides() -> None:
    """PH5-E2's corpus kind, added in E1 so the route/role tables below are
    complete from day one — E2 only has to USE the kind, never add it."""
    python_kinds = set(typing.get_args(CitationKind))
    assert "document" in python_kinds
    assert "document" in _ts_citation_kinds()


# ---------------------------------------------------------------------------
# CITATION_ROUTES / CITATION_MIN_ROLES — a missing key is a red test here, not
# a KeyError raised against a real caller at runtime.
# ---------------------------------------------------------------------------


def test_citation_routes_cover_every_kind() -> None:
    python_kinds = set(typing.get_args(CitationKind))
    assert set(CITATION_ROUTES) == python_kinds, (
        f"CITATION_ROUTES does not cover every CitationKind: "
        f"missing={python_kinds - set(CITATION_ROUTES)}, "
        f"extra={set(CITATION_ROUTES) - python_kinds}"
    )


def test_citation_min_roles_cover_every_kind() -> None:
    python_kinds = set(typing.get_args(CitationKind))
    assert set(CITATION_MIN_ROLES) == python_kinds, (
        f"CITATION_MIN_ROLES does not cover every CitationKind: "
        f"missing={python_kinds - set(CITATION_MIN_ROLES)}, "
        f"extra={set(CITATION_MIN_ROLES) - python_kinds}"
    )


def test_citation_min_roles_are_never_empty() -> None:
    """An empty role set would mean "no caller may ever open this" — silently
    dead evidence, on a kind some tool presumably still emits."""
    for kind, roles in CITATION_MIN_ROLES.items():
        assert roles, f"{kind!r} permits no role at all"


def test_citation_routes_matches_the_typescript_table() -> None:
    """Same key-set (and value) parity as ``CitationKind`` itself, for the one
    table the frontend also needs — a href template typed differently on the
    two sides would 404 silently rather than fail a build."""
    ts_routes = _ts_citation_routes()
    assert set(CITATION_ROUTES) == set(ts_routes), (
        f"CITATION_ROUTES (Python) and CITATION_ROUTES (TS) cover different "
        f"kinds: python only={set(CITATION_ROUTES) - set(ts_routes)}, "
        f"ts only={set(ts_routes) - set(CITATION_ROUTES)}"
    )
    assert ts_routes == CITATION_ROUTES, (
        "CITATION_ROUTES (Python) and CITATION_ROUTES (TS) name different "
        "route templates for at least one kind"
    )
