"""Re-derive the facts that docs/ACCEPTED-RISKS.md and the ignore lists rest on.

WHY THIS EXISTS. Two dependency advisories are accepted rather than fixed
(scripts/pip-audit-ignore.txt): python-jose CVE-2026-85394, which is CRITICAL,
and langgraph-sdk CVE-2026-104873. Neither acceptance is "we judged the risk
tolerable" — each is a claim ABOUT THIS CODEBASE that makes the vulnerability
unreachable:

  * python-jose: the advisory needs "algorithms not explicitly restricted", and
    every `jwt.decode` here passes `algorithms=`.
  * langgraph-sdk: the advisory needs `actions=` on `@auth.on.*` decorators, and
    the SDK is not imported, no such decorator exists, and there is no
    langgraph.json.

A claim like that is one commit away from being false, and nothing would say so.
The python-jose entry names its own re-check condition — "a decode added
anywhere without `algorithms=`" — and this script is that condition, enforced.

It is a REVIEWER'S TOOL as much as a gate. Everything it prints is a fact a
security-auditor would otherwise have to take on trust from a pull request
description, which is the wrong way round for a CRITICAL acceptance.

WHAT IT CANNOT CHECK, said plainly so a green run is not read as more than it
is: whether the advisories' own descriptions are still accurate, whether a NEW
advisory affects these packages, and the per-state write-set vector in AR-10
residue 1 (that needs a live database — the method is in the entry). Nor does it
check the pinned versions against PyPI; a pin CHANGE is caught here, but whether
a fix has since been published is the dependency audit's job.
"""

from __future__ import annotations

import ast
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]

# The versions each acceptance was written against. A bump is not a failure of
# the product — it is a reason to re-read the entry, because the reasoning was
# checked against these exact releases.
PINNED = {
    "python-jose": "3.5.0",
    "langgraph": "0.2.76",
    "langgraph-sdk": "0.1.74",
}

# Where production code lives. Tests are excluded deliberately: a test may
# construct a deliberately-unsafe decode to prove a guard works, and counting it
# here would make this script fight the suite.
PROD_GLOBS = ("services/*/app/**/*.py", "shared/**/*.py")


def _prod_files() -> list[pathlib.Path]:
    out: list[pathlib.Path] = []
    for pattern in PROD_GLOBS:
        for p in ROOT.glob(pattern):
            parts = p.parts
            if any(seg in {"tests", ".venv", "__pycache__"} for seg in parts):
                continue
            out.append(p)
    return sorted(out)


def _decode_calls(path: pathlib.Path) -> list[tuple[int, bool]]:
    """Every `<something>.decode(...)` that is a JWT decode, and whether it
    restricts algorithms.

    AST rather than a substring search, for the reason this repo has learned
    repeatedly: `"algorithms=" in line` is a proxy. It is true of a comment, of
    a call three lines away, and false of a call whose keyword sits on the next
    line. The parse answers the actual question — does THIS call carry that
    keyword.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[tuple[int, bool]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute) or func.attr != "decode":
            continue
        # `jwt.decode(...)` / `jose_jwt.decode(...)` — a JWT library call, not
        # `bytes.decode()`. Keyed on the receiver's name containing "jwt".
        recv = func.value
        name = recv.id if isinstance(recv, ast.Name) else getattr(recv, "attr", "")
        if "jwt" not in name.lower():
            continue
        restricted = any(k.arg == "algorithms" for k in node.keywords)
        found.append((node.lineno, restricted))
    return found


def check_jwt_decodes(problems: list[str]) -> list[str]:
    """python-jose CVE-2026-85394: every JWT decode restricts algorithms."""
    notes: list[str] = []
    total = 0
    for path in _prod_files():
        for lineno, restricted in _decode_calls(path):
            total += 1
            rel = path.relative_to(ROOT).as_posix()
            if restricted:
                notes.append(f"  {rel}:{lineno} decode restricts algorithms")
            else:
                problems.append(
                    f"{rel}:{lineno}: a JWT decode with NO `algorithms=`. This "
                    "invalidates the python-jose CVE-2026-85394 acceptance in "
                    "scripts/pip-audit-ignore.txt, whose whole basis is that the "
                    "advisory's precondition — 'algorithms not explicitly "
                    "restricted' — is absent here. python-jose has no patched "
                    "release, so this is not a finding to fix later: either "
                    "restrict the algorithms or re-open that acceptance."
                )
    if total == 0:
        problems.append(
            "no JWT decode found in production code at all. Either the auth "
            "stack moved (good — check whether the python-jose acceptance is "
            "still needed) or this script's AST matcher stopped recognising it "
            "(bad — it would now pass vacuously)."
        )
    notes.insert(0, f"  {total} JWT decode call(s) in production code")
    return notes


def check_langgraph_sdk_unused(problems: list[str]) -> list[str]:
    """langgraph-sdk CVE-2026-104873: the affected API is not used here."""
    notes: list[str] = []

    importers = [
        p.relative_to(ROOT).as_posix()
        for p in _prod_files()
        if re.search(r"^\s*(?:from|import)\s+langgraph_sdk", p.read_text(encoding="utf-8"), re.M)
    ]
    if importers:
        problems.append(
            "langgraph_sdk is imported in production code "
            f"({', '.join(importers)}). The CVE-2026-104873 acceptance rests on "
            "it being transitive-only; re-read that entry."
        )
    else:
        notes.append("  langgraph_sdk imported nowhere in production code")

    decorated = [
        f"{p.relative_to(ROOT).as_posix()}"
        for p in _prod_files()
        if re.search(r"@auth\.on\b", p.read_text(encoding="utf-8"))
    ]
    if decorated:
        problems.append(
            f"@auth.on.* decorators found ({', '.join(decorated)}). "
            "CVE-2026-104873 affects exactly these when `actions=` is used — the "
            "acceptance assumed none exist."
        )
    else:
        notes.append("  no @auth.on.* decorator anywhere")

    platform = [p.as_posix() for p in ROOT.glob("langgraph.json")] + [
        p.as_posix() for p in ROOT.glob("*/langgraph.json")
    ]
    if platform:
        problems.append(
            f"langgraph.json present ({', '.join(platform)}): there IS a "
            "LangGraph Platform deployment, so the SDK's auth layer is live and "
            "CVE-2026-104873 needs re-assessing."
        )
    else:
        notes.append("  no langgraph.json — no LangGraph Platform deployment")
    return notes


def check_pins(problems: list[str]) -> list[str]:
    """The acceptances were reasoned against exact releases. Flag a change."""
    notes: list[str] = []
    for pkg, expected in PINNED.items():
        seen: dict[str, list[str]] = {}
        for req in sorted(ROOT.glob("services/*/requirements.txt")):
            for line in req.read_text(encoding="utf-8").splitlines():
                m = re.match(rf"^{re.escape(pkg)}==([^\s#]+)", line.strip())
                if m:
                    seen.setdefault(m.group(1), []).append(
                        req.parent.name
                    )
        if not seen:
            notes.append(f"  {pkg}: not pinned anywhere (acceptance may be moot)")
            continue
        for version, services in sorted(seen.items()):
            if version != expected:
                problems.append(
                    f"{pkg} is pinned at {version} in {', '.join(services)}, but "
                    f"the accepted-risk entry was reasoned against {expected}. "
                    "Re-read the entry and update this script's PINNED map in the "
                    "same change — a bump may have fixed it, or moved it."
                )
            else:
                notes.append(f"  {pkg}=={version} ({len(services)} services)")

    # multidict must stay under 7.0: aiohttp requires <7.0, and 6.9.1 is the
    # release that carries the CVE-2026-104874 fix.
    for req in sorted(ROOT.glob("services/*/requirements.txt")):
        for line in req.read_text(encoding="utf-8").splitlines():
            m = re.match(r"^multidict==(\d+)\.(\d+)\.(\d+)", line.strip())
            if not m:
                continue
            major, minor = int(m.group(1)), int(m.group(2))
            if major != 6 or minor < 9:
                problems.append(
                    f"{req.parent.name}: multidict=={m.group(0).split('==')[1]} — "
                    "CVE-2026-104874 is fixed in 6.9.1 and aiohttp requires "
                    "<7.0, so this needs to be 6.9.x."
                )
    notes.append("  multidict 6.9.x in every service that pins it")
    return notes


def main() -> int:
    problems: list[str] = []
    sections = [
        ("python-jose CVE-2026-85394 — algorithms are restricted", check_jwt_decodes),
        ("langgraph-sdk CVE-2026-104873 — affected API unused", check_langgraph_sdk_unused),
        ("pinned versions the acceptances were reasoned against", check_pins),
    ]
    for title, fn in sections:
        print(f"{title}:")
        for note in fn(problems):
            print(note)

    if problems:
        print("\nAn accepted risk's own justification no longer holds:\n", file=sys.stderr)
        for p in problems:
            print(f"  - {p}\n", file=sys.stderr)
        print(
            "These are not style findings. Each one is a sentence in "
            "scripts/pip-audit-ignore.txt or docs/ACCEPTED-RISKS.md that has "
            "stopped being true, which means a vulnerability those files call "
            "unreachable may now be reachable.",
            file=sys.stderr,
        )
        return 1

    print("\nOK - every accepted-risk justification still holds.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
