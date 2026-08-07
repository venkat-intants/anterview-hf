"""Pin: the production worker does NOT depend on the ``app.graph`` prompt island.

Why this test exists (IC-2, code review 2026-08-07). An architecture doc claimed
``graph/prompts.py`` was "shared by both, used by the worker". It is not — the
worker builds its prompt in ``_interviewer_instructions()``. The doc was
corrected, but a corrected sentence in Markdown is not a control: it drifts back
the moment someone skims the wrong paragraph.

The failure mode the claim causes is specifically dangerous in the security
direction. Someone hardening ``graph/prompts.py`` — adding injection framing
around ``resume_text`` / ``jd_text`` / candidate speech (IC-1) — would believe
they had protected the live interview. They would not have: every LiveKit
session renders its prompt in ``interview_worker.py``, which never imports the
graph. Hardening applied to the island protects nothing that ships.

So the claim is asserted here instead, where CI enforces it.

Static, not runtime, on purpose. A ``sys.modules`` check after importing the
worker would (a) miss nothing but (b) be polluted by whatever the rest of the
suite imported first, and (c) see only module-level imports — while this
module's real risk includes the function-level imports the worker does lazily
(``app.llm.gemini``, ``app.database``, …). Walking the AST catches an import
wherever it is written, including inside a function or under ``TYPE_CHECKING``.
"""

from __future__ import annotations

import ast
from pathlib import Path

# services/interview_core — tests/unit/<this file>
_SERVICE_ROOT = Path(__file__).resolve().parents[2]
_APP_ROOT = _SERVICE_ROOT / "app"

_WORKER_MODULE = "app.worker.interview_worker"
_FORBIDDEN_MODULE = "app.graph.prompts"


def _module_path(module: str) -> Path | None:
    """Return the file backing a first-party ``app.*`` module, if it exists.

    Returns ``None`` for third-party modules and for names that are objects
    rather than modules (``from app.models import Job`` yields a candidate
    ``app.models.Job``, which has no file).
    """
    if module != "app" and not module.startswith("app."):
        return None
    relative = Path(*module.split("."))
    candidate = _SERVICE_ROOT / relative.with_suffix(".py")
    if candidate.is_file():
        return candidate
    package = _SERVICE_ROOT / relative / "__init__.py"
    return package if package.is_file() else None


def _imports_in(source: str, *, package: str) -> set[str]:
    """Every ``app.*`` module name an AST references, at any nesting depth."""
    found: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                # Relative import — resolve against the containing package.
                parts = package.split(".")
                base = ".".join(parts[: len(parts) - node.level + 1])
                module = f"{base}.{node.module}" if node.module else base
            else:
                module = node.module or ""
            if not module:
                continue
            found.add(module)
            # ``from app.graph import prompts`` names a SUBMODULE, not an
            # attribute — without this the island could be pulled in by a form
            # the walker does not see. Non-module names are filtered out later
            # by _module_path returning None.
            found.update(f"{module}.{alias.name}" for alias in node.names)
    return found


def _transitive_app_imports(entry: str) -> set[str]:
    """Breadth-first closure of first-party imports reachable from ``entry``."""
    seen: set[str] = set()
    queue: list[str] = [entry]
    while queue:
        module = queue.pop()
        if module in seen:
            continue
        path = _module_path(module)
        if path is None:
            continue
        seen.add(module)
        package = module if path.name == "__init__.py" else module.rsplit(".", 1)[0]
        for imported in _imports_in(path.read_text(encoding="utf-8"), package=package):
            if imported not in seen:
                queue.append(imported)
    return seen


def test_worker_import_walker_sees_the_real_dependencies() -> None:
    """Positive control — a broken walker must not make the pin vacuous.

    If ``_transitive_app_imports`` silently returned an empty set (wrong root,
    parse failure swallowed, resolver bug), the pin below would pass forever
    while asserting nothing. These three are imports the worker demonstrably
    has: ``app.avatars`` and ``app.config`` at module level, ``app.llm.gemini``
    inside a function — so this also proves the walker descends into function
    bodies, which is where half the worker's first-party imports live.
    """
    reachable = _transitive_app_imports(_WORKER_MODULE)

    assert _WORKER_MODULE in reachable, (
        f"{_WORKER_MODULE} did not resolve to a file under {_APP_ROOT} — the "
        "walker is broken, not the worker. Fix the resolver before trusting "
        "the pin below."
    )
    for expected in ("app.avatars", "app.config", "app.llm.gemini"):
        assert expected in reachable, (
            f"{expected!r} is missing from the worker's transitive import set. "
            "Either the worker genuinely stopped importing it, or the AST "
            "walker regressed and the injection pin below is now vacuous."
        )


def test_worker_does_not_import_the_graph_prompt_island() -> None:
    """``interview_worker`` must NOT reach ``app.graph.prompts``.

    See the module docstring: this is the assertion that stops the corrected
    architecture doc from drifting back into the false claim.
    """
    reachable = _transitive_app_imports(_WORKER_MODULE)

    assert _FORBIDDEN_MODULE not in reachable, (
        f"{_WORKER_MODULE} now reaches {_FORBIDDEN_MODULE}.\n\n"
        "WHY THIS MATTERS: the two interview paths render prompts "
        "independently. Production (every LiveKit session) uses "
        "interview_worker._interviewer_instructions(); graph/prompts.py is an "
        "island reachable only through the unexecuted app.agent import chain. "
        "Anyone who hardens graph/prompts.py — injection framing around "
        "resume_text / jd_text / candidate speech — is NOT protecting "
        "production while that stays true.\n\n"
        "If you wired the graph into the worker deliberately, that is a real "
        "architecture change: update docs/ARCH-realtime-interview.md, confirm "
        "the island's untrusted-input framing is at least as strong as "
        "_interviewer_instructions(), and then change this test on purpose."
    )
