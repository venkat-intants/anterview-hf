"""B4 / D-06 — one applicant per person per company, keyed by email.

DB is mocked. What is under test is the logic around the rule: who counts as
"the same person", what happens to an address read off a CV that already
belongs to someone else, and that the unique index only ever switches itself
on. The real index, the merge and the upload reuse run against Postgres in
``tests/integration/smoke_group_b_identity.py``.
"""

from __future__ import annotations

import importlib.util
import uuid
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.exc import IntegrityError

_MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "alembic" / "versions" / "20260912_0003_d1f3a5b7c9e2_applicant_identity.py"
)


def _db(**kw: object) -> AsyncMock:
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=kw.get("scalar"))
    db.execute = AsyncMock()
    db.commit = AsyncMock()
    nested = MagicMock()
    nested.__aenter__ = AsyncMock(return_value=None)
    nested.__aexit__ = AsyncMock(return_value=False)
    db.begin_nested = MagicMock(return_value=nested)
    return db


class _Applicant:
    def __init__(self, **kw: object) -> None:
        self.full_name = kw.get("full_name", "cv_2024_final")
        self.full_name_source = kw.get("full_name_source", "filename")
        self.parsed_full_name = None
        self.email = kw.get("email")
        self.parsed_email = None
        self.pending_enrichment = kw.get("pending_enrichment", True)
        self.updated_at = datetime(2020, 1, 1, tzinfo=UTC)


# ===========================================================================
# The index DDL — migration and runtime must build the same rule
# ===========================================================================
def test_the_runtime_index_is_the_migrations_index() -> None:
    """Two copies of one rule. If they drift, an install that got the index
    from the migration enforces something different from one that got it
    later from the catch-up job."""
    from app.requisitions import IDENTITY_INDEX_DDL

    spec = importlib.util.spec_from_file_location("_identity_migration", _MIGRATION)
    assert spec is not None and spec.loader is not None
    mig = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mig)
    assert mig.IDENTITY_INDEX_DDL.split() == IDENTITY_INDEX_DDL.split()


def test_the_index_never_forces_itself_over_duplicates() -> None:
    """Refusing to boot would take the Space down over data only HR can fix."""
    from app.requisitions import IDENTITY_INDEX_DDL

    ddl = " ".join(IDENTITY_INDEX_DDL.split())
    assert "HAVING count(*) > 1 ) THEN RAISE NOTICE" in ddl
    assert "RAISE EXCEPTION" not in ddl
    # Soft-deleted rows (merged-away duplicates) must not count against it.
    assert "WHERE deleted_at IS NULL AND email IS NOT NULL" in ddl


@pytest.mark.asyncio
@pytest.mark.parametrize("exists", [True, False])
async def test_ensure_index_reports_whether_the_rule_is_on(exists: bool) -> None:
    from app.requisitions import ensure_applicant_identity_index

    db = _db(scalar=1 if exists else None)
    assert await ensure_applicant_identity_index(db) is exists
    db.commit.assert_awaited()


# ===========================================================================
# applicant_by_email — who counts as the same person
# ===========================================================================
@pytest.mark.asyncio
@pytest.mark.parametrize("email", [None, "", "   "])
async def test_no_email_is_nobody(email: str | None) -> None:
    """Blank addresses never match each other — two people who left the field
    empty are not one person."""
    from app.requisitions import applicant_by_email

    db = _db(scalar=uuid.uuid4())
    assert await applicant_by_email(db, company_id=uuid.uuid4(), email=email) is None
    db.scalar.assert_not_awaited()


@pytest.mark.asyncio
async def test_matching_is_by_company_and_ignores_case_and_spacing() -> None:
    from app.requisitions import applicant_by_email

    found, company, me = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    db = _db(scalar=found)
    got = await applicant_by_email(db, company_id=company, email=" Asha@X.in ", exclude_id=me)
    assert got == found
    sql, params = str(db.scalar.call_args.args[0]), db.scalar.call_args.args[1]
    assert "company_id = :c" in sql, "identity must never cross tenants"
    assert "deleted_at IS NULL" in sql
    assert "lower(btrim(email)) = lower(btrim(:em))" in sql
    assert params == {"c": company, "em": " Asha@X.in ", "x": me}


# ===========================================================================
# apply_extracted_identity — an address that belongs to someone else
# ===========================================================================
def test_a_cv_address_owned_by_someone_else_is_kept_aside() -> None:
    """Not dropped (the review screen needs it to propose the merge) and not
    written to ``email`` (that would be a second applicant for one person)."""
    from app.applicant_enrichment import apply_extracted_identity

    a = _Applicant()
    assert apply_extracted_identity(a, {"candidate_email": "asha@x.in"}, email_taken=True)  # type: ignore[arg-type]
    assert a.email is None
    assert a.parsed_email == "asha@x.in"


def test_a_free_cv_address_becomes_the_email() -> None:
    from app.applicant_enrichment import apply_extracted_identity

    a = _Applicant()
    apply_extracted_identity(a, {"candidate_email": "asha@x.in"})  # type: ignore[arg-type]
    assert a.email == "asha@x.in"
    assert a.parsed_email is None


def test_a_typed_email_is_never_touched_either_way() -> None:
    from app.applicant_enrichment import apply_extracted_identity

    a = _Applicant(email="typed@x.in")
    apply_extracted_identity(a, {"candidate_email": "cv@x.in"}, email_taken=True)  # type: ignore[arg-type]
    assert (a.email, a.parsed_email) == ("typed@x.in", None)


# ===========================================================================
# Merge — duplicates found through the kept-aside address, and resolved
# ===========================================================================
def test_merge_candidates_include_kept_aside_addresses() -> None:
    from app.requisitions import _MERGE_SQL

    sql = " ".join(_MERGE_SQL.split())
    assert "COALESCE(NULLIF(btrim(email), ''), parsed_email)" in sql


def _merge_db(promote: object = None) -> AsyncMock:
    db = _db(scalar=2)
    ok = MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[]))),
                   rowcount=1)

    async def _execute(stmt: object, *_: object, **__: object) -> MagicMock:
        if "SET email = parsed_email" in str(stmt) and promote is not None:
            raise promote  # type: ignore[misc]
        return ok

    db.execute = AsyncMock(side_effect=_execute)
    return db


@pytest.mark.asyncio
async def test_merge_gives_the_survivor_its_kept_aside_address() -> None:
    from app.requisitions import merge_applicants

    db = _merge_db()
    await merge_applicants(db, company_id=uuid.uuid4(), survivor_id=uuid.uuid4(),
                           absorbed_ids=[uuid.uuid4()], actor_user_id=uuid.uuid4())
    sqls = [" ".join(str(c.args[0]).split()) for c in db.execute.call_args_list]
    promote = [s for s in sqls if "SET email = parsed_email" in s]
    assert promote, "survivor's parsed_email never promoted"
    # Only into an empty slot — a typed address is never replaced by a CV guess.
    assert "(email IS NULL OR btrim(email) = '')" in promote[0]
    # After the absorbed rows are retired, or the index would still see them.
    assert sqls.index(promote[0]) > next(i for i, s in enumerate(sqls) if "SET deleted_at" in s)


@pytest.mark.asyncio
async def test_a_still_shared_address_does_not_fail_the_merge() -> None:
    """A third, unmerged row may still hold the address. The merge itself
    must still succeed; the survivor just keeps it aside."""
    from app.requisitions import merge_applicants

    db = _merge_db(promote=IntegrityError("UPDATE", {}, Exception("dup")))
    got = await merge_applicants(db, company_id=uuid.uuid4(), survivor_id=uuid.uuid4(),
                                 absorbed_ids=[uuid.uuid4()], actor_user_id=uuid.uuid4())
    assert set(got) == {"enrolments", "assignments", "attempts", "invites"}
