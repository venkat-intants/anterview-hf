#!/usr/bin/env python3
"""Fail the build when a PUBLISHED metric-registry lock entry is edited or removed.

WHY THIS EXISTS
----------------
``services/data_gateway/app/metrics/published.lock.json`` is PH5-C2's frozen
record of every ``flag``/``measure``/``metric``/``dimension``/``rule`` version
that has ever shipped: one entry per ``kind:name@version``, each a hash of
that version's canonical spec. That is the guarantee behind "metric versions
cannot silently change historical results" — but a hash-in-a-JSON-file is only
a guarantee if something outside the file itself refuses to let an entry
change. A unit test inside the same PR that edited the entry proves nothing;
this runs from the MERGE BASE, so it catches the case the unit test cannot:
a definition changed and its own lock entry updated to match, in the same
commit, on the theory that keeping them consistent is enough.

WHAT IS CHECKED
----------------
Every entry present in the lock file AT THE MERGE BASE (see :func:`merge_base_ref`
for exactly which commit that is) must still be present, with an IDENTICAL
value, in the current working tree. A NEW entry (a version that did not exist
at the merge base) is always fine — this is an append-only guarantee, not a
frozen file.

THE MERGE-BASE COMMIT, PRECISELY (code review fix)
A first version of this script chose the base commit by BRANCH NAME
(``HEAD^1`` when the current branch is literally named ``main``, else
``merge-base HEAD origin/main``). That never fires the ``HEAD^1`` path in
CI: ``actions/checkout`` leaves a DETACHED HEAD, so
``git rev-parse --abbrev-ref HEAD`` prints ``HEAD``, never ``main``, even on
a push straight to main — and on that push, ``origin/main`` already points at
the same commit HEAD does, so ``merge-base(HEAD, origin/main)`` resolves to
HEAD itself and the lock is compared with itself, catching nothing. The fix
compares SHAs, not names: whenever HEAD and ``origin/main`` are the SAME
commit (or ``origin/main`` cannot be resolved at all — a shallow clone with no
remote, or a checkout with no known upstream), ``HEAD^1`` — the commit
immediately before this one — is used instead, which still catches a bad
edit arriving IN that push. If HEAD has no parent either (this repository's
very first commit), there is nothing to compare against; that is reported as
a NOTE, not a failure.

A RENAME IS NOT A CLEAN SLATE
If the lock file does not exist at the base ref, THE NORMAL READING is "this
change adds the file for the first time" — nothing to protect yet, so an
empty base is not a failure. But an EMPTY base is also indistinguishable from
"this change is what MOVED the file" (the git history at the old path exists,
just not at ``LOCK_REL``), and in that failure mode the append-only guarantee
would have been silently bypassed by a rename that carries an edited entry
along with it. So the base tree is also checked, with ``git ls-tree``, for a
``published.lock.json`` at ANY OTHER path — if one exists, this refuses
outright rather than comparing against nothing.

This script is pure stdlib plus ``git`` (the job's gate scripts must stay
dependency-free — see ``check_env_parity.py``), and is linted by the
``invariants`` job's own ``ruff check --config shared/pyproject.toml ops/``
step rather than a step of its own.

Run::

    python ops/ci/check_metric_lock.py
"""

from __future__ import annotations

import json
import subprocess  # nosec B404 — `git show`/`merge-base`/`rev-parse` only, no shell, no user input
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
LOCK_REL = "services/data_gateway/app/metrics/published.lock.json"

RENAME_MESSAGE = (
    "The metric lock moved; the append-only check cannot compare across a rename."
)


def _run_git(args: list[str], repo_root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # nosec B603 B607 — fixed argv, no shell, no interpolation
        ["git", *args], cwd=repo_root, capture_output=True, text=True, check=False,
    )


def _rev_parse(ref: str, repo_root: Path) -> str | None:
    """The commit SHA ``ref`` resolves to, or ``None`` if it does not resolve
    at all (an unknown ref, a missing remote, or — for ``HEAD^1`` — a commit
    with no parent)."""
    proc = _run_git(["rev-parse", ref], repo_root)
    return proc.stdout.strip() if proc.returncode == 0 else None


def merge_base_ref(repo_root: Path) -> str | None:
    """The commit to diff the lock file against, or ``None`` if there isn't one.

    See the module docstring's "THE MERGE-BASE COMMIT, PRECISELY" section for
    why this compares SHAs rather than branch names.
    """
    head = _rev_parse("HEAD", repo_root)
    origin_main = _rev_parse("origin/main", repo_root)
    if head is not None and origin_main is not None and origin_main != head:
        proc = _run_git(["merge-base", "HEAD", "origin/main"], repo_root)
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout.strip()
    # Either HEAD already IS origin/main's tip (a push straight to main, seen
    # by CI as a detached HEAD — see the module docstring), or there is no
    # resolvable origin/main at all. HEAD^1 still catches a bad edit arriving
    # in the same commit/push; it resolves to None only when HEAD has no
    # parent (the repository's first commit).
    return _rev_parse("HEAD^1", repo_root)


def _lock_exists_at_ref(ref: str, repo_root: Path) -> bool:
    proc = _run_git(["cat-file", "-e", f"{ref}:{LOCK_REL}"], repo_root)
    return proc.returncode == 0


def _lock_at_ref(ref: str, repo_root: Path) -> dict[str, Any]:
    """The lock file's parsed content at ``ref`` — ``{}`` if it did not exist
    there yet or is not valid JSON at that point in history."""
    proc = _run_git(["show", f"{ref}:{LOCK_REL}"], repo_root)
    if proc.returncode != 0:
        return {}
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _lock_moved_in_base_tree(ref: str, repo_root: Path) -> bool:
    """True if a file named ``published.lock.json`` exists ANYWHERE in the
    tree at ``ref`` other than at :data:`LOCK_REL` — the rename guard."""
    proc = _run_git(["ls-tree", "-r", "--name-only", ref], repo_root)
    if proc.returncode != 0:
        return False
    filename = LOCK_REL.rsplit("/", 1)[-1]
    return any(
        line != LOCK_REL and line.rsplit("/", 1)[-1] == filename
        for line in proc.stdout.splitlines()
    )


def _current_lock(repo_root: Path) -> dict[str, Any]:
    """The lock file as it stands in the WORKING TREE right now."""
    path = repo_root / LOCK_REL
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


class LockCheckResult:
    """Either a clean bill (``violations == []``), a set of append-only
    violations, or a single fatal ``note`` that is not itself a diff (no
    prior commit to compare against, or the rename guard)."""

    def __init__(self, violations: list[str], note: str | None = None) -> None:
        self.violations = violations
        self.note = note

    @property
    def failed(self) -> bool:
        return bool(self.violations)


def check(repo_root: Path) -> LockCheckResult:
    """Every violation found: a merge-base entry that is missing, or whose
    value differs, in the current lock file."""
    base_ref = merge_base_ref(repo_root)
    if base_ref is None:
        return LockCheckResult(
            [], note="No prior commit to compare against (this is the repository's "
                     "first commit) — nothing to check."
        )

    if not _lock_exists_at_ref(base_ref, repo_root) and _lock_moved_in_base_tree(
        base_ref, repo_root
    ):
        return LockCheckResult([RENAME_MESSAGE])

    base_lock = _lock_at_ref(base_ref, repo_root)
    current_lock = _current_lock(repo_root)

    violations: list[str] = []
    for key, entry in sorted(base_lock.items()):
        if key not in current_lock:
            violations.append(f"{key} was REMOVED (present at {base_ref}).")
        elif current_lock[key] != entry:
            violations.append(
                f"{key} was MODIFIED since {base_ref}: {entry} -> {current_lock[key]}."
            )
    return LockCheckResult(violations)


def main(repo_root: Path | None = None) -> int:
    root = repo_root or REPO_ROOT
    result = check(root)
    if result.failed:
        print(f"FAIL: {LOCK_REL} broke its append-only guarantee:")
        for violation in result.violations:
            print(f"  - {violation}")
        print(
            "A published flag/measure/metric/dimension/rule version is frozen once "
            "it lands on main. To change a definition, add a NEW version with a "
            "later effective_from and a change_note; never edit, remove or move "
            "the entry an existing version already published."
        )
        return 1
    if result.note:
        print(f"OK ({result.note})")
    else:
        print(f"OK: {LOCK_REL} — no published entry was modified or removed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
