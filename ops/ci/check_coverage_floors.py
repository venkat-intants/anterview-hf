#!/usr/bin/env python3
"""Fail the build when the coverage floors drift between the three places they live.

Why this script exists
----------------------
The coverage floors have now drifted TWICE in the same way. The first time,
``ci.yml``'s own explanatory comment documented ``55/68/83/78`` while the
``case`` block ten lines below it enforced ``50/62/78/72``. The comment that had
been written to prevent exactly that said "KEEP THIS TABLE AND THE ``case``
BELOW IN STEP" — and it did not, because a comment cannot enforce itself. The
second time (code review 2026-08-07, **CI-1**) ``docs/LLD.md`` disagreed with
``ci.yml`` on three of the four floors, and ``ci.yml``'s comment vouched for a
synchronisation with LLD.md that no longer held.

So the invariant is made executable here instead of asserted in prose. Three
sources must agree:

1. ``.github/workflows/ci.yml`` — the ``case`` block. **The only executable
   value**; everything else is a description of it.
2. ``.github/workflows/ci.yml`` — the ``was -> now -> floor  margin`` comment
   table in the same step. This is what drifted the first time.
3. ``docs/LLD.md`` — the table under the ``<!-- COVERAGE-FLOORS ... -->``
   marker. This is what drifted the second time, and it is the one a bid reader
   sees.

It also re-derives ``margin = measured - floor`` rather than trusting the
written margin, and cross-checks the service list against the CI matrix, so a
fifth service added to the matrix without a floor cannot pass unnoticed.

Run locally exactly as CI runs it::

    python ops/ci/check_coverage_floors.py

Exit status 0 = the three agree. 1 = they do not; the differences are printed.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CI_YML = REPO_ROOT / ".github" / "workflows" / "ci.yml"
LLD_MD = REPO_ROOT / "docs" / "LLD.md"

# The marker anchors the parser to ONE table. Without it the script would latch
# onto whichever markdown table happened to come first, and a harmless edit
# elsewhere in a 2,700-line document would fail the build for the wrong reason.
LLD_MARKER = "<!-- COVERAGE-FLOORS"

# `data_gateway)     FLOOR=55 ;;`
_CASE_RE = re.compile(r"^\s*([a-z_]+)\)\s*FLOOR=(\d+)\s*;;\s*$", re.MULTILINE)

# `#   data_gateway     59  ->  60  ->  55      5`
_COMMENT_RE = re.compile(
    r"^\s*#\s+([a-z_]+)\s+(\d+)\s*->\s*(\d+)\s*->\s*(\d+)\s+(\d+)\s*$",
    re.MULTILINE,
)

# `        service: [data_gateway, interview_core, feedback_billing, admin_ops]`
_MATRIX_RE = re.compile(r"^\s*service:\s*\[([^\]]+)\]\s*$", re.MULTILINE)

# `| `data_gateway` | 60% | 55% | 5 | No |`
_LLD_ROW_RE = re.compile(
    r"^\|\s*`?([a-z_]+)`?\s*\|\s*(\d+)%?\s*\|\s*(\d+)%?\s*\|\s*(\d+)\s*\|",
)


def _read(path: Path) -> str:
    if not path.is_file():
        sys.exit(f"FAIL: {path.relative_to(REPO_ROOT)} does not exist.")
    return path.read_text(encoding="utf-8")


def parse_ci_case(text: str) -> dict[str, int]:
    """Floors as CI actually enforces them: ``{service: floor}``."""
    return {svc: int(floor) for svc, floor in _CASE_RE.findall(text)}


def parse_ci_comment(text: str) -> dict[str, tuple[int, int, int]]:
    """The ci.yml comment table: ``{service: (measured, floor, margin)}``.

    ``was`` (the previous measurement) is history, not an invariant, so it is
    parsed and discarded — re-measuring must not require touching this script.
    """
    return {
        svc: (int(now), int(floor), int(margin))
        for svc, _was, now, floor, margin in _COMMENT_RE.findall(text)
    }


def parse_ci_matrix(text: str) -> list[str]:
    match = _MATRIX_RE.search(text)
    if not match:
        sys.exit("FAIL: could not find the `service: [...]` matrix in ci.yml.")
    return [s.strip() for s in match.group(1).split(",") if s.strip()]


def parse_lld_table(text: str) -> dict[str, tuple[int, int, int]]:
    """The LLD.md table: ``{service: (measured, floor, margin)}``."""
    marker_at = text.find(LLD_MARKER)
    if marker_at == -1:
        sys.exit(
            f"FAIL: docs/LLD.md has no {LLD_MARKER}...--> marker.\n"
            "      That marker is what ties the documented floors to the enforced\n"
            "      ones. Do not remove it; update the table under it instead."
        )
    rows: dict[str, tuple[int, int, int]] = {}
    # Stop at the first line that is not part of a markdown table, so a later
    # table in the document can never be absorbed into this one.
    for line in text[marker_at:].splitlines()[1:]:
        stripped = line.strip()
        if not stripped.startswith("|"):
            if rows:
                break
            continue
        match = _LLD_ROW_RE.match(stripped)
        if match:
            svc, measured, floor, margin = match.groups()
            rows[svc] = (int(measured), int(floor), int(margin))
    return rows


def main() -> int:
    ci_text = _read(CI_YML)
    lld_text = _read(LLD_MD)

    enforced = parse_ci_case(ci_text)
    documented_ci = parse_ci_comment(ci_text)
    documented_lld = parse_lld_table(lld_text)
    matrix = parse_ci_matrix(ci_text)

    problems: list[str] = []

    if not enforced:
        problems.append(
            "ci.yml: no `<service>) FLOOR=<n> ;;` lines found — the Coverage step "
            "was reshaped and this checker no longer sees the floors it guards."
        )
    if not documented_lld:
        problems.append(
            f"docs/LLD.md: no table rows parsed under {LLD_MARKER}. Expected rows "
            "shaped `| `service` | <measured>% | <floor>% | <margin> | ... |`."
        )

    missing_floor = sorted(set(matrix) - set(enforced))
    if missing_floor:
        problems.append(
            "ci.yml: these services are in the test matrix but have no coverage "
            f"floor in the `case` block: {', '.join(missing_floor)}. An unmatched "
            "service falls through `case` with FLOOR unset and `coverage report "
            "--fail-under=` then fails the step with a confusing error."
        )

    for svc in sorted(enforced):
        floor = enforced[svc]

        if svc not in documented_ci:
            problems.append(
                f"{svc}: enforced floor {floor} has no row in the ci.yml comment "
                "table (the `was -> now -> floor  margin` block)."
            )
        else:
            measured, doc_floor, margin = documented_ci[svc]
            if doc_floor != floor:
                problems.append(
                    f"{svc}: ci.yml comment says floor {doc_floor}, the `case` "
                    f"enforces {floor}."
                )
            if measured - doc_floor != margin:
                problems.append(
                    f"{svc}: ci.yml comment margin is {margin} but "
                    f"{measured} - {doc_floor} = {measured - doc_floor}."
                )

        if svc not in documented_lld:
            problems.append(
                f"{svc}: enforced floor {floor} has no row in the docs/LLD.md "
                "coverage table."
            )
        else:
            measured, doc_floor, margin = documented_lld[svc]
            if doc_floor != floor:
                problems.append(
                    f"{svc}: docs/LLD.md says floor {doc_floor}, ci.yml enforces "
                    f"{floor}."
                )
            if measured - doc_floor != margin:
                problems.append(
                    f"{svc}: docs/LLD.md margin is {margin} but "
                    f"{measured} - {doc_floor} = {measured - doc_floor}."
                )

    for svc in sorted(set(documented_lld) - set(enforced)):
        problems.append(
            f"{svc}: documented in docs/LLD.md but no floor is enforced in ci.yml."
        )

    # The measured numbers are a point-in-time measurement, not a contract, so
    # they are allowed to be re-measured — but the two documents must be
    # re-measured TOGETHER, which is the drift this catches.
    for svc in sorted(set(documented_ci) & set(documented_lld)):
        if documented_ci[svc][0] != documented_lld[svc][0]:
            problems.append(
                f"{svc}: measured coverage is {documented_ci[svc][0]}% in ci.yml "
                f"and {documented_lld[svc][0]}% in docs/LLD.md."
            )

    if problems:
        print("Coverage-floor invariant BROKEN:\n", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        print(
            "\nThe `case` block in .github/workflows/ci.yml is the only value that\n"
            "executes. Fix the two descriptions to match it (or change all three in\n"
            "the same commit). Floors may be RAISED freely; lowering one needs a\n"
            "reason in the commit message.",
            file=sys.stderr,
        )
        return 1

    print("Coverage floors agree across ci.yml (case), ci.yml (comment) and docs/LLD.md:")
    for svc in sorted(enforced):
        measured, floor, margin = documented_lld[svc]
        print(f"  {svc:<18} measured {measured}%  floor {floor}%  margin {margin}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
