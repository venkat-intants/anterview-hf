"""Tests for ``ops/ci/check_metric_lock.py``.

The gate is only worth its runtime if it can go RED. Every test below either
builds a real, throwaway git repository and asserts the exit status, or pins
the real repository's own lock file as currently clean — mocking the git
calls away would test nothing, since the thing under test IS the git diff.

Run::

    python -m pytest ops/ci/tests -q
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "ops" / "ci" / "check_metric_lock.py"

_ENTRY_A = {"sha256": "a" * 64, "effective_from": "2026-09-22", "added_on": "2026-09-22"}
_ENTRY_A_CHANGED = {"sha256": "b" * 64, "effective_from": "2026-09-22", "added_on": "2026-09-22"}
_ENTRY_B = {"sha256": "c" * 64, "effective_from": "2026-09-22", "added_on": "2026-09-22"}


def _load() -> ModuleType:
    """Import the script fresh — ops/ is not a package, and this keeps every
    test's REPO_ROOT default independent of any other test's import."""
    spec = importlib.util.spec_from_file_location("check_metric_lock", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def checker() -> ModuleType:
    return _load()


def _git(args: list[str], cwd: Path) -> None:
    subprocess.run(  # nosec B603 B607 — test helper, fixed argv, throwaway tmp repo
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True,
    )


def _init_repo(tmp_path: Path) -> Path:
    """A throwaway repo on branch ``main`` with no commits yet — deliberately
    ``main``, so :func:`merge_base_ref` takes the ``HEAD^1`` path without
    needing an ``origin`` remote this test has no reason to set up."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(["init", "-q"], repo)
    _git(["config", "user.email", "test@example.com"], repo)
    _git(["config", "user.name", "Test"], repo)
    _git(["checkout", "-q", "-b", "main"], repo)
    return repo


def _write_lock(repo: Path, entries: dict) -> None:
    lock_dir = repo / "services" / "data_gateway" / "app" / "metrics"
    lock_dir.mkdir(parents=True, exist_ok=True)
    (lock_dir / "published.lock.json").write_text(
        json.dumps(entries, indent=2, sort_keys=True), encoding="utf-8",
    )


def _commit(repo: Path, message: str) -> None:
    _git(["add", "-A"], repo)
    _git(["commit", "-q", "-m", message], repo)


def _git_output(args: list[str], cwd: Path) -> str:
    proc = subprocess.run(  # nosec B603 B607 — test helper, fixed argv, throwaway tmp repo
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True,
    )
    return proc.stdout.strip()


def _init_origin_and_clone(tmp_path: Path) -> tuple[Path, Path]:
    """An ``origin`` repo carrying the initial lock, and a real clone of it —
    so ``clone`` has a genuine ``origin/main`` remote-tracking ref, the shape
    :func:`merge_base_ref`'s primary path actually reads in CI (a plain
    from-scratch repo, as ``_init_repo`` builds, never has one at all)."""
    origin = tmp_path / "origin"
    origin.mkdir()
    _git(["init", "-q"], origin)
    _git(["config", "user.email", "test@example.com"], origin)
    _git(["config", "user.name", "Test"], origin)
    _git(["checkout", "-q", "-b", "main"], origin)
    _write_lock(origin, {"flag:a@1": _ENTRY_A})
    _commit(origin, "initial")

    clone = tmp_path / "clone"
    _git(["clone", "-q", str(origin), str(clone)], tmp_path)
    _git(["config", "user.email", "test@example.com"], clone)
    _git(["config", "user.name", "Test"], clone)
    return origin, clone


# ===========================================================================
# The real repository
# ===========================================================================
def test_the_real_lock_file_is_currently_clean(checker: ModuleType) -> None:
    """The committed tree must be green, or CI is red on arrival."""
    assert checker.main() == 0


# ===========================================================================
# A throwaway repository
# ===========================================================================
def test_a_modified_entry_fails(tmp_path: Path, checker: ModuleType) -> None:
    repo = _init_repo(tmp_path)
    _write_lock(repo, {"flag:a@1": _ENTRY_A})
    _commit(repo, "first")
    _write_lock(repo, {"flag:a@1": _ENTRY_A_CHANGED})
    _commit(repo, "second")
    assert checker.main(repo) == 1


def test_a_removed_entry_fails(tmp_path: Path, checker: ModuleType) -> None:
    repo = _init_repo(tmp_path)
    _write_lock(repo, {"flag:a@1": _ENTRY_A})
    _commit(repo, "first")
    _write_lock(repo, {})
    _commit(repo, "second")
    assert checker.main(repo) == 1


def test_a_new_entry_passes(tmp_path: Path, checker: ModuleType) -> None:
    """Append-only, not frozen: a version that did not exist before is fine."""
    repo = _init_repo(tmp_path)
    _write_lock(repo, {"flag:a@1": _ENTRY_A})
    _commit(repo, "first")
    _write_lock(repo, {"flag:a@1": _ENTRY_A, "flag:b@1": _ENTRY_B})
    _commit(repo, "second")
    assert checker.main(repo) == 0


def test_an_untouched_entry_is_fine_even_if_the_file_was_never_committed_again(
    tmp_path: Path, checker: ModuleType,
) -> None:
    """The check reads the WORKING TREE, not the last commit — an unchanged
    file that happens to be rewritten byte-for-byte is not a violation."""
    repo = _init_repo(tmp_path)
    _write_lock(repo, {"flag:a@1": _ENTRY_A})
    _commit(repo, "first")
    _write_lock(repo, {"flag:a@1": _ENTRY_A})  # rewritten, identical, never committed
    assert checker.main(repo) == 0


def test_first_commit_ever_has_nothing_to_protect(tmp_path: Path, checker: ModuleType) -> None:
    """Before any prior commit carried the file, there is no merge-base entry
    to compare against — the check must not fail on the file's own birth."""
    repo = _init_repo(tmp_path)
    _write_lock(repo, {"flag:a@1": _ENTRY_A})
    _commit(repo, "adds the lock file for the first time")
    assert checker.main(repo) == 0


# ===========================================================================
# The primary path: a feature branch with a REAL origin/main remote
# ===========================================================================
def test_primary_path_feature_branch_modified_entry_fails(
    tmp_path: Path, checker: ModuleType,
) -> None:
    _origin, clone = _init_origin_and_clone(tmp_path)
    _git(["checkout", "-q", "-b", "feature"], clone)
    _write_lock(clone, {"flag:a@1": _ENTRY_A_CHANGED})
    _commit(clone, "feature: modify a published entry")
    assert checker.main(clone) == 1


def test_primary_path_feature_branch_new_entry_passes(
    tmp_path: Path, checker: ModuleType,
) -> None:
    _origin, clone = _init_origin_and_clone(tmp_path)
    _git(["checkout", "-q", "-b", "feature"], clone)
    _write_lock(clone, {"flag:a@1": _ENTRY_A, "flag:b@1": _ENTRY_B})
    _commit(clone, "feature: add a new entry")
    assert checker.main(clone) == 0


# ===========================================================================
# The actual CI shape: a detached HEAD sitting exactly at origin/main's tip
# (what a push straight to main looks like once actions/checkout runs)
# ===========================================================================
def test_detached_head_at_origin_main_tip_modified_entry_fails(
    tmp_path: Path, checker: ModuleType,
) -> None:
    _origin, clone = _init_origin_and_clone(tmp_path)
    _write_lock(clone, {"flag:a@1": _ENTRY_A_CHANGED})
    _commit(clone, "lands directly on main")
    head_sha = _git_output(["rev-parse", "HEAD"], clone)
    # Simulate the remote-tracking ref actions/checkout would already show
    # for a just-pushed main, without needing push permissions into a
    # checked-out branch.
    _git(["update-ref", "refs/remotes/origin/main", head_sha], clone)
    _git(["checkout", "-q", "--detach", "HEAD"], clone)
    assert checker.main(clone) == 1


def test_detached_head_at_origin_main_tip_new_entry_passes(
    tmp_path: Path, checker: ModuleType,
) -> None:
    _origin, clone = _init_origin_and_clone(tmp_path)
    _write_lock(clone, {"flag:a@1": _ENTRY_A, "flag:b@1": _ENTRY_B})
    _commit(clone, "lands directly on main")
    head_sha = _git_output(["rev-parse", "HEAD"], clone)
    _git(["update-ref", "refs/remotes/origin/main", head_sha], clone)
    _git(["checkout", "-q", "--detach", "HEAD"], clone)
    assert checker.main(clone) == 0


# ===========================================================================
# The rename guard
# ===========================================================================
def test_a_renamed_lock_file_is_refused(tmp_path: Path, checker: ModuleType) -> None:
    """The lock existed, at a DIFFERENT path, at the base commit — not "born
    in this change". Comparing against an empty base here would silently let
    a rename carry an edited entry straight past the append-only check."""
    repo = _init_repo(tmp_path)
    old_path = repo / "archive" / "published.lock.json"
    old_path.parent.mkdir(parents=True)
    old_path.write_text(json.dumps({"flag:a@1": _ENTRY_A}), encoding="utf-8")
    _commit(repo, "lock lived elsewhere")

    old_path.unlink()
    old_path.parent.rmdir()
    _write_lock(repo, {"flag:a@1": _ENTRY_A_CHANGED})
    _commit(repo, "moved to the canonical path (and silently modified)")

    assert checker.main(repo) == 1


def test_a_renamed_lock_file_with_no_change_is_still_refused(
    tmp_path: Path, checker: ModuleType,
) -> None:
    """Even an honest, unmodified move is refused: the script cannot tell
    "moved, unchanged" from "moved, tampered with" without diffing against
    the old path too, which is exactly the comparison an attacker's rename
    is designed to dodge. Refusing outright keeps the guarantee — a real
    rename is a one-line change to LOCK_REL in this script, reviewed like
    any other."""
    repo = _init_repo(tmp_path)
    old_path = repo / "archive" / "published.lock.json"
    old_path.parent.mkdir(parents=True)
    old_path.write_text(json.dumps({"flag:a@1": _ENTRY_A}), encoding="utf-8")
    _commit(repo, "lock lived elsewhere")

    old_path.unlink()
    old_path.parent.rmdir()
    _write_lock(repo, {"flag:a@1": _ENTRY_A})
    _commit(repo, "moved to the canonical path, unchanged")

    assert checker.main(repo) == 1
