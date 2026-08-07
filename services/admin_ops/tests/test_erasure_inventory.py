"""DPDP-7: the erasure table inventory must not be able to drift silently.

The finding was not "three tables are missing". It was that the executor's
inventory was PROSE — a list of what somebody had thought about — so a table
added by a later migration joined neither the erasure path nor the exclusion
list, and nothing anywhere noticed. A one-time audit fixes the three tables and
re-arms the same defect on the next migration.

So these tests hold ``ERASED_TABLES`` and ``EXCLUDED_TABLES`` against the schema
of record and fail if they do not partition it exactly. Adding a table in a
migration turns this suite red until someone writes down which side it belongs
on and why — which is the only form of "complete inventory" that stays true.

Discovery is by ``ast``, not by import: ``services/data_gateway`` has no package
``__init__`` chain, its ``app`` package name collides with admin_ops's own, and
importing it would pull in pydantic settings that need a live environment. The
parse reads two sources, because neither alone is complete:

  * ``services/data_gateway/app/models.py`` — the ORM mirror, and the file the
    finding names as the schema of record;
  * ``services/data_gateway/alembic/versions/`` — what actually exists in the
    database. ``integrity_events`` and ``feature_flags`` are declared ONLY here
    (the ORM mirror never grew them), and both carry a user FK. A models-only
    check would have passed while missing them.
"""

from __future__ import annotations

import ast
from pathlib import Path

from app.erasure_executor import ERASED_TABLES, EXCLUDED_TABLES

# tests/ -> admin_ops/ -> services/ -> repo root
_REPO_ROOT = Path(__file__).resolve().parents[3]
_MODELS_PY = _REPO_ROOT / "services" / "data_gateway" / "app" / "models.py"
_MIGRATIONS_DIR = _REPO_ROOT / "services" / "data_gateway" / "alembic" / "versions"


def _orm_tables() -> set[str]:
    """Every ``__tablename__ = "..."`` literal in the data_gateway ORM."""
    tree = ast.parse(_MODELS_PY.read_text(encoding="utf-8"))
    tables: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not isinstance(node.value, ast.Constant) or not isinstance(node.value.value, str):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == "__tablename__":
                tables.add(node.value.value)
    return tables


def _migration_tables() -> set[str]:
    """Every table name passed to ``op.create_table(...)`` in the alembic chain.

    Only the first positional argument is read, and only when it is a string
    literal — a computed name would be invisible here, which is a limitation
    worth knowing about rather than working around: this repo has never written
    one, and a migration that did would be a review finding of its own.
    """
    tables: set[str] = set()
    for path in sorted(_MIGRATIONS_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not isinstance(func, ast.Attribute) or func.attr != "create_table":
                continue
            if not isinstance(func.value, ast.Name) or func.value.id != "op":
                continue
            if node.args and isinstance(node.args[0], ast.Constant):
                first = node.args[0].value
                if isinstance(first, str):
                    tables.add(first)
    return tables


def _schema_tables() -> set[str]:
    return _orm_tables() | _migration_tables()


# ---------------------------------------------------------------------------
# Guard on the discovery itself
#
# Every assertion below is of the form "this set difference is empty", and an
# empty discovery satisfies all of them. A moved file or a renamed decorator
# would therefore turn the whole DPDP-7 control green by making it test nothing.
# ---------------------------------------------------------------------------


def test_schema_discovery_actually_found_the_schema() -> None:
    orm = _orm_tables()
    migrations = _migration_tables()
    assert "users" in orm, f"ORM discovery found {sorted(orm)} — did {_MODELS_PY} move?"
    assert "users" in migrations, (
        f"migration discovery found {sorted(migrations)} — did {_MIGRATIONS_DIR} move?"
    )
    # 26 in the ORM at the time of writing; well under that means the walk broke.
    assert len(_schema_tables()) >= 25


def test_migrations_declare_tables_the_orm_mirror_does_not() -> None:
    """Pins WHY discovery reads both sources, so nobody simplifies it away.

    ``models.py`` says so itself ("Treat alembic/versions/ as the source of
    truth"). If this ever becomes an empty set the ORM has caught up and the
    migration scan is belt-and-braces — but it is not empty today, and the two
    tables it names are exactly the ones a models-only inventory would miss.
    """
    missing_from_orm = _migration_tables() - _orm_tables()
    assert "integrity_events" in missing_from_orm
    assert "feature_flags" in missing_from_orm


# ---------------------------------------------------------------------------
# The inventory partition
# ---------------------------------------------------------------------------


def test_every_schema_table_is_assigned() -> None:
    """The DPDP-7 fix: no table may be silently unaccounted for."""
    unassigned = _schema_tables() - set(ERASED_TABLES) - set(EXCLUDED_TABLES)
    assert not unassigned, (
        "These tables exist in the data_gateway schema but appear in neither "
        f"ERASED_TABLES nor EXCLUDED_TABLES: {sorted(unassigned)}. Decide for "
        "each one whether the erasure executor must reach it, then add it to "
        "app/erasure_executor.py with the reason. Under DPDP §12 an "
        "incomplete-but-declared exclusion is defensible; an undeclared one is "
        "not."
    )


def test_inventory_names_no_table_the_schema_does_not_have() -> None:
    """Catches the other drift direction — a rename or a dropped table.

    Without this, a table renamed in a migration leaves a stale entry that still
    reads like a decision while the real table is unassigned, and the entry
    keeps a reviewer from looking any further.
    """
    known = _schema_tables()
    phantom = (set(ERASED_TABLES) | set(EXCLUDED_TABLES)) - known
    assert not phantom, (
        f"The inventory names tables that no longer exist: {sorted(phantom)}. "
        "They were probably renamed — check whether the new name is assigned."
    )


def test_a_table_is_either_erased_or_excluded_never_both() -> None:
    overlap = set(ERASED_TABLES) & set(EXCLUDED_TABLES)
    assert not overlap, f"Contradictory inventory entries: {sorted(overlap)}"


def test_every_entry_carries_a_reason() -> None:
    """An exclusion with no reason is an undeclared exclusion wearing a label."""
    for name, reason in {**ERASED_TABLES, **EXCLUDED_TABLES}.items():
        assert len(reason.strip()) >= 20, f"{name!r} has no usable reason: {reason!r}"


def test_tables_the_executor_writes_to_are_on_the_erased_side() -> None:
    """Ties the lists to the code, so the inventory cannot describe a different
    executor than the one that runs.

    Reads the module source rather than executing it: the executor's statements
    are ``text()`` SQL built inline, and asserting on the source is what makes
    "someone deleted the DELETE" visible here instead of only in a mock.
    """
    source = (Path(__file__).resolve().parents[1] / "app" / "erasure_executor.py").read_text(
        encoding="utf-8"
    )
    for table in ("turns", "resumes", "scorecards", "sessions", "notifications"):
        assert f"DELETE FROM {table} " in source, (
            f"{table} is listed in ERASED_TABLES but the executor no longer "
            "deletes from it"
        )
    assert "UPDATE applicants " in source
    assert "UPDATE users SET " in source
