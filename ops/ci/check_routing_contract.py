#!/usr/bin/env python3
"""Every router prefix reaches a backend, and every token header is redacted.

Two lists in this repo have to be edited by hand whenever a route or a custom
header is added, and nothing checked either of them. Both have now lagged the
code more than once:

  * ``/apply*`` and ``/careers*`` — the entire public candidate surface, added
    in 076fe06 — were in NEITHER Caddyfile. On the Space an XHR to a prefix with
    no ``handle`` block falls through to ``try_files {path} /index.html`` and
    comes back as index.html with **HTTP 200**, so ``res.ok`` is true and
    ``res.json()`` throws on the HTML. A whole feature ships dead and reports
    success. The repo has been bitten by exactly this before: 09483b5,
    "fix: route /agent/* to data_gateway — the agent layer was dead on the Space".

  * ``X-Draft-Token`` reached neither Caddy log filter nor the CORS allow-list.
    The first puts a live 30-day credential — read, write, CV replacement,
    delete and submit over a named candidate's PII — verbatim into a persistent
    access log, undoing the #fragment design that exists to keep tokens out of
    logs (CWE-532). The second blocks every draft call at preflight on any
    split-origin deploy, while the same-origin Space stays green.

Neither failure is reachable from any test that runs a service: they live in
config files, and the Space variant fails as a 200. So they are asserted here,
statically, in the job that gates the merge.

Scope, stated honestly: this checks that each prefix is matched by SOME
``handle`` block, not that it is matched by the RIGHT one. Upstream correctness
for the deliberately-shared prefixes (``/admin``, ``/api``, ``/users``) is
Caddy's longest-path-wins and is not modelled here. The failure this exists to
catch is "reaches no backend at all", which is the one that has actually
happened twice.

Exit 0 = the contract holds. Exit 1 = it does not, with the fix printed.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SERVICES = ("interview_core", "data_gateway", "feedback_billing", "admin_ops")
CADDYFILES = (ROOT / "Caddyfile", ROOT / "space" / "Caddyfile")
CORS_FILE = ROOT / "services" / "data_gateway" / "app" / "main.py"

# Prefixes that must NOT be proxied. Both Caddyfiles answer these 403 on
# purpose: /internal is service-to-service only and /metrics is scraped from
# inside the network, so exposing either at the edge is the bug.
BLOCKED_BY_DESIGN = {"/internal", "/metrics"}

# /health is proxied through a rewrite to /health/live rather than a plain
# handle, and is deliberately excluded from the SPA rewrite. Checked by hand in
# both files; modelling the rewrite here would assert the parser, not the route.
NOT_CHECKED = {"/health"}

# Token headers that are deliberately NOT redacted from access logs, each with
# the reason it is safe. A new custom header is redacted or it is listed here —
# never silently neither. THINK before adding to this: the question is whether
# the header's value ALONE lets someone act as the user.
LOG_REDACTION_EXEMPT = {
    # Double-submit CSRF: the value is already in a JS-readable cookie and is
    # useless without the httpOnly refresh cookie it is compared against, which
    # Caddy redacts as `Cookie`. Logging it grants nobody anything.
    "X-CSRF-Token": "double-submit half; worthless without the httpOnly cookie",
}


def _fail(problems: list[str]) -> int:
    print("Routing / header contract BROKEN:\n", file=sys.stderr)
    for p in problems:
        print(f"  - {p}", file=sys.stderr)
    print(
        "\nBoth Caddyfiles are one routing contract; edit them together.",
        file=sys.stderr,
    )
    return 1


# ---------------------------------------------------------------- the code side
def router_prefixes() -> dict[str, set[str]]:
    """Every public path prefix each service serves.

    Read out of ``APIRouter(prefix=...)`` and ``include_router(..., prefix=...)``
    with ``ast`` rather than by import: importing a service's modules needs that
    service's whole dependency set, and this job runs on a bare checkout.
    """
    found: dict[str, set[str]] = {s: set() for s in SERVICES}
    for service in SERVICES:
        app = ROOT / "services" / service / "app"
        if not app.is_dir():
            continue
        for path in app.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except SyntaxError:  # pragma: no cover - a broken file fails elsewhere
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                fn = node.func
                name = fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", None)
                if name not in {"APIRouter", "include_router"}:
                    continue
                for kw in node.keywords:
                    if kw.arg == "prefix" and isinstance(kw.value, ast.Constant):
                        value = kw.value.value
                        if isinstance(value, str) and value.startswith("/"):
                            found[service].add(value)
    return found


def token_headers() -> dict[str, str]:
    """Custom request headers the routers read, as ``alias -> file:line``."""
    out: dict[str, str] = {}
    pattern = re.compile(r'Header\(\s*alias="(X-[A-Za-z0-9-]+)"')
    for service in SERVICES:
        app = ROOT / "services" / service / "app"
        if not app.is_dir():
            continue
        for path in app.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                m = pattern.search(line)
                if m:
                    out.setdefault(m.group(1), f"{path.relative_to(ROOT)}:{n}")
    return out


# ---------------------------------------------------------------- the config side
_HANDLE = re.compile(r"^\s*handle\s+(\S+)\s*\{", re.MULTILINE)


def handle_blocks(text: str) -> list[tuple[str, str]]:
    """``(path matcher, body)`` for every ``handle`` block, bodies un-nested."""
    blocks: list[tuple[str, str]] = []
    for m in _HANDLE.finditer(text):
        depth, i = 1, m.end()
        while i < len(text) and depth:
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
            i += 1
        blocks.append((m.group(1), text[m.end() : i]))
    return blocks


def matches(pattern: str, path: str) -> bool:
    """Caddy path-matcher semantics: a trailing ``*`` is a prefix match."""
    return path.startswith(pattern[:-1]) if pattern.endswith("*") else path == pattern


def main() -> int:
    problems: list[str] = []
    configs = {p: p.read_text(encoding="utf-8") for p in CADDYFILES}

    # 1. Every prefix reaches a backend (or is deliberately refused).
    for service, prefixes in router_prefixes().items():
        for prefix in sorted(prefixes):
            if prefix in NOT_CHECKED:
                continue
            for path, text in configs.items():
                rel = path.relative_to(ROOT).as_posix()
                # A request one level under the prefix is the realistic probe:
                # "/users" is served by `handle /users/*`, and asking about the
                # bare prefix would report a false break.
                probe = f"{prefix.rstrip('/')}/probe"
                hit = next(
                    (b for pat, b in handle_blocks(text) if matches(pat, probe)), None
                )
                if hit is None:
                    problems.append(
                        f"{rel}: no handle block matches {prefix!r} "
                        f"({service}) — requests under it never reach the service"
                    )
                elif prefix in BLOCKED_BY_DESIGN:
                    if "respond" not in hit:
                        problems.append(
                            f"{rel}: {prefix!r} is proxied but must be refused at "
                            f"the edge (it is {service}-internal)"
                        )
                elif "reverse_proxy" not in hit:
                    problems.append(
                        f"{rel}: the handle block matching {prefix!r} ({service}) "
                        f"does not reverse_proxy"
                    )

    # 2. Every token header is redacted from both access logs.
    headers = token_headers()
    for alias, where in sorted(headers.items()):
        if alias in LOG_REDACTION_EXEMPT:
            continue
        directive = f"request>headers>{alias} delete"
        for path, text in configs.items():
            if directive not in text:
                problems.append(
                    f"{path.relative_to(ROOT).as_posix()}: {alias} (read at {where}) "
                    f"is logged verbatim — add `{directive}` to the format filter, "
                    f"or exempt it in LOG_REDACTION_EXEMPT with a reason"
                )

    # 3. Every custom header survives a CORS preflight.
    cors = CORS_FILE.read_text(encoding="utf-8")
    block = cors[cors.index("allow_headers=[") :] if "allow_headers=[" in cors else ""
    block = block[: block.index("]")] if "]" in block else block
    for alias, where in sorted(headers.items()):
        if f'"{alias}"' not in block:
            problems.append(
                f"{CORS_FILE.relative_to(ROOT).as_posix()}: {alias} (read at {where}) "
                f"is missing from allow_headers — every request carrying it fails "
                f"preflight on any split-origin deploy"
            )

    if problems:
        return _fail(problems)

    print(
        f"OK - {sum(len(v) for v in router_prefixes().values())} router prefixes "
        f"routed in both Caddyfiles; {len(headers)} custom headers "
        f"({len(LOG_REDACTION_EXEMPT)} exempt) redacted and CORS-allowed."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
