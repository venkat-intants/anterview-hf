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

A third list joined them: the edge body caps. ``/jobs*`` went in at 1MB while
``routers/jd.py`` accepts a 10 MB JD, and ``/hr/*`` at 12MB while the bulk resume
route accepts 250 MB. A cap BELOW the handler's limit 413s a legitimate upload at
the edge, so the service logs nothing and the only evidence is the 413 the user
sees — the same shape of silent failure as the two above.

Scope, stated honestly: this checks that each prefix is matched by SOME
``handle`` block, not that it is matched by the RIGHT one. For the body caps it
checks only that a cap is not too SMALL: a cap that is too large, or missing
entirely, is a judgement call this cannot make. Upstream correctness
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
# /test-hooks is the local browser tests' way to run data_gateway's background
# passes on demand instead of waiting for their timers. Its router is mounted
# only when TEST_HOOKS_ENABLED, which config refuses outside a local env; the
# edge refusal is the second, independent lock, so a mis-set env on a deployed
# box still cannot expose it.
BLOCKED_BY_DESIGN = {"/internal", "/metrics", "/test-hooks"}

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


# ---------------------------------------------------------------- body caps
#: Routes whose handler accepts an upload, mapped to the constant that caps it.
#: ``(probe path, module under services/, symbol, why)``.
#:
#: The edge cap must be >= the handler's limit, or Caddy 413s a body the service
#: would have accepted — at the edge, so the service logs nothing and the only
#: evidence is a 413 the user sees. Added after this check's absence let
#: ``/jobs*`` go in at 1MB against a 10 MB JD limit, and ``/hr/*`` at 12MB against
#: a 250 MB bulk-resume limit (review 2026-10-06).
#:
#: The VALUES come from the code so that raising a handler's limit and forgetting
#: Caddy is a failure here; only the prefix-to-symbol mapping is by hand, which is
#: the same trade every other list in this file makes.
UPLOAD_LIMITS: tuple[tuple[str, str, str, str], ...] = (
    ("/jobs/probe/jd", "data_gateway/app/routers/jd.py", "_MAX_JD_BYTES", "JD PDF"),
    (
        "/users/me/resume",
        "data_gateway/app/routers/resume.py",
        "_MAX_RESUME_BYTES",
        "candidate resume",
    ),
    (
        "/apply/probe/resume",
        "data_gateway/app/routers/public_apply.py",
        "_MAX_RESUME_BYTES",
        "pre-auth resume",
    ),
    (
        "/hr/applicants/probe/resume",
        "data_gateway/app/routers/hr_applicants.py",
        "_MAX_RESUME_BYTES",
        "single resume",
    ),
    (
        "/hr/applicants/bulk",
        "data_gateway/app/routers/hr_applicants.py",
        "_MAX_BULK_TOTAL_BYTES",
        "bulk resume batch",
    ),
    (
        "/hr/library/documents",
        "data_gateway/app/config.py",
        "corpus_document_max_bytes",
        "HR library document",
    ),
    (
        "/hr/offers/probe/document",
        "data_gateway/app/config.py",
        "preboarding_document_max_bytes",
        "preboarding document",
    ),
    (
        "/hr/rounds/probe/task/materials",
        "data_gateway/app/config.py",
        "task_material_max_bytes",
        "task material",
    ),
    ("/task/probe/response", "data_gateway/app/config.py", "task_response_max_bytes", "task response"),
)

_SIZE = re.compile(r"^\s*max_size\s+(\d+)\s*([KMG]?B)\s*$", re.MULTILINE)
_UNIT = {"B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3}


def _literal_int(node: ast.expr) -> int | None:
    """``10 * 1024 * 1024`` and ``5242880``, but nothing that needs a runtime."""
    if isinstance(node, ast.Constant) and isinstance(node.value, int):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult):
        left, right = _literal_int(node.left), _literal_int(node.right)
        return None if left is None or right is None else left * right
    return None


def handler_limit(module: str, symbol: str) -> int | None:
    """The byte limit *symbol* is assigned in *module*, by AST.

    Covers both shapes the repo uses: a module-level ``_MAX_X = 10 * 1024 * 1024``
    and a Settings field ``x_max_bytes: int = 10 * 1024 * 1024``.
    """
    path = ROOT / "services" / module
    if not path.is_file():
        return None
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign):
            target = node.target
            if isinstance(target, ast.Name) and target.id == symbol and node.value:
                return _literal_int(node.value)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == symbol:
                    return _literal_int(node.value)
    return None


def body_cap(block: str) -> int | None:
    """The ``max_size`` a handle block declares, in bytes; None when it has none."""
    m = _SIZE.search(block)
    return int(m.group(1)) * _UNIT[m.group(2)] if m else None


# ---------------------------------------------------------------- the config side
# The request-body caps, as a table CI holds both Caddyfiles to.
#
# WHY THIS IS A TABLE AND NOT PROSE. docs/ACCEPTED-RISKS.md AR-10 described
# these in prose, which went stale; the prose was replaced with an enumeration
# on 2026-10-04, and that enumeration was stale the NEXT DAY, when the VAPT pass
# retuned /apply* from 8MB to 6MB and capped four more prefixes. An enumeration
# of a moving target is just slower-rotting prose. So the list lives here, where
# a mismatch fails CI, and AR-10 points at it instead of restating it.
#
# A cap is sized from the handler's own limit. The reason a cap at the EDGE is
# the only one that works: fastapi 0.133 reads the whole request body before it
# solves a route's dependencies, so an in-handler size check and a
# `dependencies=[rate_limit(...)]` both run after an arbitrarily large body has
# been buffered — and a multipart file part lands in a SpooledTemporaryFile that
# rolls onto the container's disk. Pre-auth, pre-limit, one request.
BODY_CAPS: dict[str, str] = {
    "/apply*": "6MB",  # 5 MB CV + multipart overhead. Anonymous.
    "/auth/*": "256KB",  # JSON only, pre-authentication by nature.
    "/careers*": "256KB",  # JSON only, anonymous.
    "/exam*": "2MB",  # code_max_source_bytes 64,000 x 20 questions.
    "/interview-invite*": "256KB",  # JSON only; the invite code is the credential.
    "/interviewer/*": "1MB",  # scorecard attachments.
    "/hr/rounds/*/task/materials*": "11MB",  # job-simulation materials.
    "/offer*": "11MB",  # signed offer documents.
    "/task*": "11MB",  # job-simulation submissions.
    # Added 2026-10-06 by the upload-hardening change on `main`, and recorded
    # here in the merge that brought it. Authenticated prefixes, so these are
    # defence in depth: they bound the body FastAPI buffers before a handler
    # runs, NOT the parse — a kilobyte of crafted PDF or xlsx is the real
    # attack, which `app/pdf_text.py` is what bounds.
    "/hr/*": "12MB",  # largest single document behind it: corpus + preboarding 10MB.
    "/hr/applicants/bulk*": "260MB",  # its own block, not a bigger /hr/* number.
    "/jobs*": "11MB",  # routers/jd.py _MAX_JD_BYTES is 10MB + multipart.
    "/users/*": "6MB",  # largest upload limit behind the prefix.
}

_MAX_SIZE = re.compile(r"max_size\s+(\S+)")
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

    # 2. The request-body caps are exactly BODY_CAPS, in both files.
    for path, text in configs.items():
        rel = path.relative_to(ROOT).as_posix()
        found = {}
        for pat, body in handle_blocks(text):
            m = _MAX_SIZE.search(body)
            if m:
                found[pat] = m.group(1)
        # Only prefixes the file actually HAS. The check is about agreement
        # between the table and the config, not about a file carrying every
        # entry: `handle` blocks differ between the two deploys, and demanding
        # all nine made the gate's own minimal fixtures fail — which would have
        # left four of its tests passing for the wrong reason, since they assert
        # a non-zero exit and would have got one no matter what they broke.
        present = {pat for pat, _ in handle_blocks(text)}
        for prefix in sorted(set(found) | (set(BODY_CAPS) & present)):
            want, got = BODY_CAPS.get(prefix), found.get(prefix)
            if want == got:
                continue
            if want is None:
                problems.append(
                    f"{rel}: {prefix!r} caps bodies at {got} and is not in "
                    f"BODY_CAPS — add it there with its reason, and check "
                    f"whether docs/ACCEPTED-RISKS.md AR-10 should say so"
                )
            elif got is None:
                problems.append(
                    f"{rel}: {prefix!r} should cap bodies at {want} and has no "
                    f"request_body block — an uncapped public prefix is how an "
                    f"anonymous caller writes to this container's disk"
                )
            else:
                problems.append(
                    f"{rel}: {prefix!r} caps bodies at {got}, BODY_CAPS says "
                    f"{want} — change one, deliberately"
                )

    # 3. Every token header is redacted from both access logs.
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

    # 4. No edge cap sits below the limit the handler itself enforces.
    for probe, module, symbol, what in UPLOAD_LIMITS:
        limit = handler_limit(module, symbol)
        if limit is None:
            problems.append(
                f"ops/ci/check_routing_contract.py: UPLOAD_LIMITS names "
                f"{symbol} in {module}, which no longer has a literal value — "
                f"update the table or drop the row"
            )
            continue
        for path, text in configs.items():
            rel = path.relative_to(ROOT).as_posix()
            hit = next((b for pat, b in handle_blocks(text) if matches(pat, probe)), None)
            if hit is None:
                continue  # check 1 already reports an unrouted prefix
            cap = body_cap(hit)
            if cap is not None and cap < limit:
                problems.append(
                    f"{rel}: the handle block matching {probe!r} caps bodies at "
                    f"{cap:,} bytes, below the {limit:,} the handler allows "
                    f"({what}, {symbol}) — Caddy would 413 an upload the service "
                    f"accepts, at the edge, with nothing in the service log"
                )

    if problems:
        return _fail(problems)

    print(
        f"OK - {sum(len(v) for v in router_prefixes().values())} router prefixes "
        f"routed in both Caddyfiles; {len(headers)} custom headers "
        f"({len(LOG_REDACTION_EXEMPT)} exempt) redacted and CORS-allowed; "
        f"{len(UPLOAD_LIMITS)} upload caps at or above the handler's own limit."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
