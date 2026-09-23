"""PH5-E5 — ``CitationKind`` must name exactly the same members as the
TypeScript ``Citation.kind`` union in ``web/src/api/agent.ts``.

Nothing enforces that agreement at the type level (one is a Python Literal,
the other a TS union in a different package), so a member added to one and
not the other would only ever surface as a citation the frontend renders with
no icon or label. This test parses the TS union out of the source file and
diffs it against ``typing.get_args(CitationKind)``.
"""

from __future__ import annotations

import pathlib
import re
import typing

from shared.agents.schema import CitationKind

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
AGENT_TS = REPO_ROOT / "web" / "src" / "api" / "agent.ts"


def _ts_citation_kinds() -> set[str]:
    src = AGENT_TS.read_text(encoding="utf-8")
    # The first `kind:` union in the file is Citation's — grab the block up to
    # the closing `;` and pull every single-quoted literal out of it.
    match = re.search(r"export interface Citation \{.*?kind:\s*(.*?);", src, re.DOTALL)
    assert match is not None, "Citation.kind union not found in agent.ts"
    return set(re.findall(r"'([a-z_]+)'", match.group(1)))


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
