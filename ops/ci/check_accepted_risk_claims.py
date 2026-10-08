"""Re-derive the facts that docs/ACCEPTED-RISKS.md and the ignore lists rest on.

WHY THIS EXISTS. Some dependency advisories here are not fixed by a bump but by
a claim ABOUT THIS CODEBASE that makes the vulnerability unreachable — and a
claim like that is one commit away from being false, with nothing to say so.
This script re-derives each one.

  * langgraph-sdk CVE-2026-104873 (accepted, scripts/pip-audit-ignore.txt): the
    advisory needs `actions=` on `@auth.on.*` decorators, and the SDK is not
    imported, no such decorator exists, and there is no langgraph.json.
  * PyJWT's version floor: CVE-2026-85394 (algorithm confusion — a DER public
    key accepted as an HMAC secret) is FIXED rather than accepted, since the
    2026-10-06 migration off python-jose. But it is fixed BY A VERSION: PyJWT
    2.13.0 accepts bare DER exactly as jose did, and only 2.14.0 refuses it. So
    `>=2.14` is a security floor, and a resolver that walks under it silently
    restores a CRITICAL. That is a pin claim, checked here.
  * Every JWT decode still passes `algorithms=`. This no longer backs an
    acceptance — PyJWT raises `DecodeError` when the argument is missing, so the
    library enforces it — and it is kept because the VACUITY GUARD below is the
    thing that matters: "no decode found" and "every decode is safe" look the
    same from outside, and only one is good news.

UPDATED 2026-10-07, on merging the PyJWT migration. This file previously opened
"Two dependency advisories are accepted rather than fixed … python-jose
CVE-2026-85394, which is CRITICAL". That acceptance is gone: the dependency is.
The check it justified stayed, re-aimed at the floor that now carries the fix,
because deleting it along with the entry would have left the 2.14 boundary
unguarded in the same change that started depending on it.

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
    "PyJWT": "2.15.1",
    "langgraph": "0.2.76",
    "langgraph-sdk": "0.1.74",
}

#: The release that first refuses a bare DER public key as an HMAC secret
#: (CVE-2026-85394). Below this, the advisory is live again. Compared as a tuple
#: so 2.9 does not sort above 2.14 the way a string compare would.
PYJWT_SECURITY_FLOOR = (2, 14)

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
    """Every JWT decode restricts algorithms (CVE-2026-85394's precondition)."""
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
                    f"{rel}:{lineno}: a JWT decode with NO `algorithms=`. That is "
                    "the precondition CVE-2026-85394 needs ('algorithms not "
                    "explicitly restricted'), and it is the one shape the auth "
                    "stack must never grow. PyJWT raises DecodeError without the "
                    "argument, so this is most likely a decode that will fail at "
                    "runtime rather than a silent downgrade — but check which, "
                    "because the two look identical here."
                )
    if total == 0:
        problems.append(
            "no JWT decode found in production code at all. Either the auth "
            "stack moved again (check what replaced PyJWT, and whether this "
            "script's claims still describe it) or the AST matcher stopped "
            "recognising it (bad — it would now pass vacuously)."
        )
    notes.insert(0, f"  {total} JWT decode call(s) in production code")
    return notes


def check_pyjwt_floor(problems: list[str]) -> list[str]:
    """PyJWT >= 2.14, the release that fixed CVE-2026-85394.

    A FLOOR, not a pin, and the two need separate checks. `check_pins` catches a
    change to the exact `==` in requirements.txt; this catches the declared RANGE
    in pyproject.toml, which is what a `poetry lock` resolves against. A floor
    edited to `^2` would leave every requirements.txt untouched and still let the
    next lock walk down to 2.13.0, where a forged DER-as-HMAC token verifies.
    """
    notes: list[str] = []
    floor = ".".join(str(n) for n in PYJWT_SECURITY_FLOOR)
    found = 0
    for proj in sorted(ROOT.glob("*/pyproject.toml")) + sorted(
        ROOT.glob("services/*/pyproject.toml")
    ):
        text = proj.read_text(encoding="utf-8")
        m = re.search(r'^pyjwt\s*=.*?version\s*=\s*"\^?(\d+)\.(\d+)', text, re.M | re.I)
        if not m:
            m = re.search(r'^pyjwt\s*=\s*"\^?(\d+)\.(\d+)', text, re.M | re.I)
        if not m:
            continue
        found += 1
        rel = proj.relative_to(ROOT).as_posix()
        declared = (int(m.group(1)), int(m.group(2)))
        if declared < PYJWT_SECURITY_FLOOR:
            problems.append(
                f"{rel}: pyjwt declares >={declared[0]}.{declared[1]}, under the "
                f"{floor} security floor. CVE-2026-85394 — a DER public key "
                "accepted as an HMAC secret — is unfixed below 2.14.0, so a lock "
                "resolved against this range can reintroduce a CRITICAL with no "
                "code change and no new advisory. Raise the floor or re-open the "
                "entry in scripts/pip-audit-ignore.txt."
            )
        else:
            notes.append(f"  {rel}: pyjwt >={declared[0]}.{declared[1]} (floor {floor})")
    if found == 0:
        problems.append(
            "no pyjwt dependency declared in any pyproject.toml. Either the JWT "
            "library changed again — in which case this check and the "
            "CVE-2026-85394 note in scripts/pip-audit-ignore.txt both need "
            "re-reading — or this matcher has stopped matching and is now "
            "passing vacuously."
        )
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
        ("CVE-2026-85394 — every JWT decode restricts algorithms", check_jwt_decodes),
        ("CVE-2026-85394 — PyJWT stays above its 2.14 security floor", check_pyjwt_floor),
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
