"""The `shared` CI job enumerates paths — this asserts the enumeration is complete.

Why this file exists
--------------------
`.github/workflows/ci.yml`'s `shared` job type-checks and tests by NAMING each
path: `mypy shared/intelligence shared/agents ...` and
`pytest shared/intelligence shared/agents shared/auth shared/tests`. mypy and
pytest both take explicit arguments, so a module or a test directory that is not
listed is silently skipped — and a skipped path looks *exactly* like a path that
passed. The step reports success either way.

That is not hypothetical. It has now happened four times in this repository:

* `shared/auth` was named in the job's TITLE and absent from its `pytest` line,
  so the AUTH-01 session-revocation suite never ran anywhere.
* `shared/tests/` was created and not listed, so the metrics_auth and
  redis_factory regression suites passed locally and ran nowhere else.
* `shared/s3.py` and `shared/llm/` landed with the SVC-1 / FB-1 refactors and
  were type-checked by nothing.
* `shared/db/` and `shared/http_observability.py` — the XS-08 engine factory and
  the XS-01/XS-05/XS-06 middleware, both imported by all four services at
  startup — landed unlisted on 2026-08-07 and were found by this check.

The comments in ci.yml ask the next author to remember. Four misses say that a
comment is not a mechanism, so this test is the mechanism: add a module under
`shared/` without adding it to the workflow and this fails, in the very job the
workflow is under-covering.

Parsing, not importing
----------------------
ci.yml is read as TEXT with `re`, not with PyYAML. The `shared` job's own
install step does not include PyYAML (it installs the four services' runtime
pins and nothing more), so a yaml-based check here would be the same class of
bug it is trying to prevent: an import error that aborts collection for the
whole job.
"""

from __future__ import annotations

import pathlib
import re

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SHARED = REPO_ROOT / "shared"
CI_YAML = REPO_ROOT / ".github" / "workflows" / "ci.yml"

# Directories under shared/ that hold no code and are not expected in either
# command. shared/models and shared/voice are a bare, empty __init__.py — they
# are namespace placeholders, and mypy on an empty file checks nothing.
# Caches are build artefacts.
_NON_CODE_DIRS = {"__pycache__", ".pytest_cache", ".ruff_cache"}

# The one code path deliberately absent from the mypy line, recorded here
# rather than left as an unexplained hole. shared/auth does NOT type-check
# clean today: `redis.get` is typed `bytes | str | None` while three call sites
# in local.py annotate the result `str | None`, and one TOCTOU test uses the
# `list.append(...) or default` idiom mypy rejects. None is a runtime defect —
# `int()` accepts bytes and the idiom is intentional — but adding the path
# without fixing them would turn the job red, and a red gate gets switched off.
# Removing an entry from this set is the work; ADDING one needs a written
# reason, because every entry is coverage this repo does not have.
KNOWN_UNTYPECHECKED = {"shared/auth"}


def _shared_job_block() -> str:
    """Return just the `shared:` job, so a path named in another job cannot count."""
    text = CI_YAML.read_text(encoding="utf-8")
    start = text.index("\n  shared:\n")
    end = text.index("\n  services:\n", start)
    return text[start:end]


def _mypy_paths(block: str) -> set[str]:
    # Anchored on `run: >-` and not merely on the word "mypy": the Install step
    # above pins "mypy==1.20.2", so a looser pattern matches THERE and drags in
    # every comment line between the two steps as if it were a path.
    match = re.search(
        r"run: >-\s*\n\s*mypy\s+(.*?)\s*--ignore-missing-imports", block, re.DOTALL
    )
    assert match is not None, "the shared job no longer runs mypy — this test is stale"
    return {arg for arg in match.group(1).split() if not arg.startswith("-")}


def _pytest_paths(block: str) -> set[str]:
    match = re.search(r"run: python -m pytest (.*)", block)
    assert match is not None, "the shared job no longer runs pytest — this test is stale"
    return {arg for arg in match.group(1).split() if not arg.startswith("-")}


def _has_real_code(path: pathlib.Path) -> bool:
    """True when *path* holds at least one non-empty .py outside a tests/ dir."""
    for py in path.rglob("*.py"):
        if "tests" in py.parts or "__pycache__" in py.parts:
            continue
        if py.stat().st_size > 0:
            return True
    return False


def _code_paths() -> set[str]:
    """Every path under shared/ that holds production code, as ci.yml spells it."""
    found: set[str] = set()
    for entry in SHARED.iterdir():
        if entry.name in _NON_CODE_DIRS:
            continue
        if entry.is_file() and entry.suffix == ".py":
            # __init__.py is reached via the package argument, never named alone.
            if entry.name != "__init__.py" and entry.stat().st_size > 0:
                found.add(f"shared/{entry.name}")
        elif entry.is_dir() and entry.name != "tests" and _has_real_code(entry):
            found.add(f"shared/{entry.name}")
    return found


def _test_dirs() -> set[str]:
    """Every directory under shared/ that actually contains test modules."""
    return {
        f"shared/{test.relative_to(SHARED).parent.as_posix()}"
        for test in SHARED.rglob("test_*.py")
        if "__pycache__" not in test.parts
    }


def test_ci_yaml_is_where_we_think_it_is() -> None:
    """Guard the guard: if the path drifts, every set below comes back empty.

    A checker that silently stops matching prints OK forever, which is the
    failure mode this whole file is about.
    """
    assert CI_YAML.is_file(), f"{CI_YAML} not found — parents[2] no longer the repo root"
    assert "\n  shared:\n" in CI_YAML.read_text(encoding="utf-8")


def test_every_shared_code_path_is_type_checked() -> None:
    listed = _mypy_paths(_shared_job_block())
    missing = _code_paths() - listed - KNOWN_UNTYPECHECKED
    assert not missing, (
        "these paths under shared/ hold code that the CI `shared` job's "
        f"Type-check step never sees: {sorted(missing)}. mypy takes explicit "
        "arguments — unlisted means unchecked, and it looks identical to a pass. "
        "Add them to the `mypy ...` line in .github/workflows/ci.yml."
    )


def test_type_check_list_names_only_paths_that_exist() -> None:
    """The complement: a renamed or removed module leaves a dead argument behind.

    mypy errors on a missing path, so this would eventually fail the job anyway —
    but it fails there with a bare "cannot find" and fails here with the reason.
    """
    stale = [p for p in _mypy_paths(_shared_job_block()) if not (REPO_ROOT / p).exists()]
    assert not stale, f"ci.yml type-checks paths that no longer exist: {sorted(stale)}"


def test_every_shared_test_directory_is_collected() -> None:
    listed = _pytest_paths(_shared_job_block())
    missing = {
        directory
        for directory in _test_dirs()
        # A directory is covered by itself or by any listed ancestor:
        # shared/auth/tests is collected because shared/auth is listed.
        if not any(directory == arg or directory.startswith(arg + "/") for arg in listed)
    }
    assert not missing, (
        "these directories hold tests that the CI `shared` job never collects: "
        f"{sorted(missing)}. Add them to the `pytest ...` line in "
        ".github/workflows/ci.yml — the suites pass locally and run nowhere."
    )


@pytest.mark.parametrize("module", ["fastapi", "prometheus_client", "starlette"])
def test_http_observability_dependencies_are_installed(module: str) -> None:
    """shared/http_observability.py imports these at MODULE level.

    Not a tautology when run in CI: this job builds its environment from an
    explicit `pip install` list, and a missing entry there aborts pytest at
    COLLECTION — no test fails, the whole job simply never runs and reports
    success on zero tests. That has happened three times (email-validator,
    aioboto3/httpx, then these three). Naming them as an assertion means the
    Install step and the code cannot drift apart silently.
    """
    __import__(module)
