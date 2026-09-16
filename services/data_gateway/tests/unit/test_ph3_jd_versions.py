"""Job descriptions are versioned, not overwritten — PH3-B3 / PH3-B6.

The load-bearing property is the last section: editing an advert must not change
what a published workflow measures. Group C froze ``round_criteria`` at
authoring time exactly so a JD edit could not retrospectively re-grade someone
who had already sat a round, and both PH3-B3 and PH3-B6 name that as a
guardrail. It is asserted here rather than hoped for.
"""

from __future__ import annotations

import inspect
import uuid
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

APP = Path(__file__).resolve().parents[2] / "app"
MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "alembic" / "versions" / "20260916_0002_d4f6b8c0e2a5_ph3_b3_jd_versions.py"
)

REQ = uuid.UUID("11111111-1111-1111-1111-111111111111")
COMPANY = uuid.UUID("22222222-2222-2222-2222-222222222222")
ACTOR = uuid.UUID("33333333-3333-3333-3333-333333333333")

LIVE = {
    "jd_text": "Build and run the platform.",
    "responsibilities": ["Ship features"],
    "required_skills": ["Python"],
    "nice_to_have_skills": [],
}


def _version_row(**kw: object) -> dict:
    """A jd_versions row as SELECT * actually returns one.

    Complete on purpose: the module's contract is "a row read back from the
    table", and a fixture that omits columns would let a KeyError in real code
    pass here.
    """
    base = {
        "id": uuid.uuid4(),
        "company_id": COMPANY,
        "requisition_id": REQ,
        "version": 2,
        "status": "draft",
        "change_note": None,
        "created_by_user_id": ACTOR,
        "created_at": datetime(2026, 9, 16, 9, 0, tzinfo=UTC),
        "updated_at": datetime(2026, 9, 16, 9, 0, tzinfo=UTC),
        "published_at": None,
        "superseded_at": None,
        **LIVE,
    }
    return {**base, **kw}


def _db(
    *,
    version_row: dict | None = None,
    draft_row: dict | None = None,
    live: dict | None = None,
) -> AsyncMock:
    """A fake session that answers by WHAT WAS ASKED, not by call order.

    A positional queue breaks the moment the code under test adds a statement,
    which turns a refactor into a test failure that says nothing. Dispatching on
    the SQL means these tests assert on behaviour rather than on how many round
    trips it happens to take.
    """
    db = AsyncMock()

    async def _execute(statement: object, *_a: object, **_k: object) -> MagicMock:
        sql = getattr(statement, "text", str(statement))
        row: dict | None
        if "FROM job_requisitions" in sql and "SELECT" in sql:
            row = live if live is not None else dict(LIVE)
        elif "status = 'draft'" in sql and sql.strip().startswith("SELECT"):
            row = draft_row
        elif sql.strip().startswith("SELECT"):
            row = version_row
        else:
            row = None
        res = MagicMock()
        mapped = MagicMock()
        mapped.first = MagicMock(return_value=row)
        mapped.all = MagicMock(return_value=[row] if row else [])
        res.mappings = MagicMock(return_value=mapped)
        res.rowcount = 1
        return res

    db.execute = AsyncMock(side_effect=_execute)
    db.scalar = AsyncMock(return_value=1)
    return db


# ===========================================================================
# The whole advert is a version, not just the prose
# ===========================================================================
def test_a_version_captures_every_field_that_makes_up_the_advert() -> None:
    """A version that kept the prose while the skills list moved underneath it
    would preserve the wrong half."""
    from app.jd_versions import JD_FIELDS

    assert set(JD_FIELDS) == {
        "jd_text", "responsibilities", "required_skills", "nice_to_have_skills",
    }


def test_the_migration_versions_the_same_fields_the_code_does() -> None:
    from app.jd_versions import JD_FIELDS

    sql = MIGRATION.read_text(encoding="utf-8")
    for field in JD_FIELDS:
        assert field in sql, field


def test_a_patch_that_does_not_touch_the_advert_mints_no_version() -> None:
    """A history of no-op entries is a history nobody reads."""
    from app.jd_versions import touches_jd

    assert touches_jd({"closes_at": "2026-10-01"}) is False
    assert touches_jd({"title": "New title"}) is False
    assert touches_jd({"jd_text": "..."}) is True
    assert touches_jd({"required_skills": ["Go"]}) is True


# ===========================================================================
# Numbering and the single-published invariant
# ===========================================================================
def test_the_database_allows_only_one_published_version() -> None:
    """The invariant that makes published_jd_version_id trustworthy. Enforced
    by a partial unique index, not by every writer remembering to demote."""
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "uq_jd_versions_one_published" in sql
    assert "status = 'published'" in sql
    assert "unique=True" in sql


def test_the_database_allows_only_one_draft() -> None:
    """"The draft" is singular in the UI, so two of them has no correct
    rendering."""
    assert "uq_jd_versions_one_draft" in MIGRATION.read_text(encoding="utf-8")


def test_a_published_row_and_its_timestamp_cannot_disagree() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "ck_jd_versions_published_at" in sql


@pytest.mark.asyncio
async def test_version_numbers_come_from_the_maximum_not_a_count() -> None:
    """A discarded draft must leave a gap rather than let the next version
    reuse a number a reader has already seen."""
    from app.jd_versions import _next_version

    db = AsyncMock()
    db.scalar = AsyncMock(return_value=7)
    assert await _next_version(db, requisition_id=REQ) == 8
    assert "max(version)" in db.scalar.await_args.args[0].text


@pytest.mark.asyncio
async def test_the_first_version_of_an_opening_is_one() -> None:
    from app.jd_versions import _next_version

    db = AsyncMock()
    db.scalar = AsyncMock(return_value=None)
    assert await _next_version(db, requisition_id=REQ) == 1


@pytest.mark.asyncio
async def test_publishing_demotes_the_old_version_before_promoting_the_new() -> None:
    """The unique index is checked per statement, so the other order collides
    mid-transaction."""
    from app.jd_versions import publish_version

    row = _version_row(status="draft")
    db = _db(version_row=row)
    await publish_version(
        db, requisition_id=REQ, company_id=COMPANY,
        version_id=row["id"], actor_user_id=ACTOR,
    )
    statements = [c.args[0].text for c in db.execute.await_args_list]
    # Matched precisely: the demote statement's own WHERE clause also mentions
    # status = 'published' (it is what it looks for), so a loose search finds
    # one statement and reports it as both.
    demote = next(i for i, s in enumerate(statements) if "SET status = 'archived'" in s)
    promote = next(i for i, s in enumerate(statements) if "SET status = 'published'" in s)
    assert demote < promote


@pytest.mark.asyncio
async def test_publishing_copies_the_content_onto_the_requisition() -> None:
    """The live document follows the published version in the SAME transaction,
    so the two can never be observed disagreeing — and every existing reader
    keeps finding the JD where it has always been."""
    from app.jd_versions import publish_version

    row = _version_row(status="draft")
    db = _db(version_row=row)
    await publish_version(
        db, requisition_id=REQ, company_id=COMPANY,
        version_id=row["id"], actor_user_id=ACTOR,
    )
    statements = [c.args[0].text for c in db.execute.await_args_list]
    assert any(
        "UPDATE job_requisitions" in s and "published_jd_version_id" in s
        for s in statements
    )


@pytest.mark.asyncio
async def test_publishing_an_already_published_version_is_a_no_op() -> None:
    from app.jd_versions import publish_version

    row = _version_row(status="published", published_at=datetime(2026, 9, 1, tzinfo=UTC))
    db = _db(version_row=row)
    result = await publish_version(
        db, requisition_id=REQ, company_id=COMPANY,
        version_id=row["id"], actor_user_id=ACTOR,
    )
    assert result["status"] == "published"
    assert db.execute.await_count == 1  # only the lookup


@pytest.mark.asyncio
async def test_publishing_a_version_from_another_company_is_refused() -> None:
    """Scoped in the WHERE clause, so a cross-tenant id reads as missing."""
    from app.jd_versions import publish_version

    db = _db(version_row=None)
    with pytest.raises(LookupError):
        await publish_version(
            db, requisition_id=REQ, company_id=COMPANY,
            version_id=uuid.uuid4(), actor_user_id=ACTOR,
        )
    assert "company_id = :c" in db.execute.await_args_list[0].args[0].text


# ===========================================================================
# Drafts do not reach the public
# ===========================================================================
@pytest.mark.asyncio
async def test_saving_a_draft_never_touches_the_requisition() -> None:
    """This is the entire reason the public cannot see a draft: the public
    surfaces read job_requisitions, and a draft does not write to it."""
    from app.jd_versions import save_draft

    # No draft yet on the first lookup; one after the insert. Modelled by
    # letting the draft lookup answer with a row throughout — what this test
    # asserts is which TABLES were written, not the lookup sequence.
    db = _db(draft_row=_version_row())
    await save_draft(
        db, requisition_id=REQ, company_id=COMPANY, actor_user_id=ACTOR,
        content={"jd_text": "A revision nobody should see yet."},
    )
    for call in db.execute.await_args_list:
        assert "UPDATE job_requisitions" not in call.args[0].text


@pytest.mark.asyncio
async def test_a_second_save_updates_the_draft_rather_than_making_another() -> None:
    """Eleven saves must not burn eleven version numbers and make the history
    read as eleven revisions."""
    from app.jd_versions import save_draft

    existing = _version_row()
    db = _db(draft_row=existing)
    await save_draft(
        db, requisition_id=REQ, company_id=COMPANY, actor_user_id=ACTOR,
        content={"jd_text": "Second thoughts."},
    )
    statements = [c.args[0].text for c in db.execute.await_args_list]
    assert not any("INSERT INTO jd_versions" in s for s in statements)
    assert any("UPDATE jd_versions" in s for s in statements)


@pytest.mark.asyncio
async def test_a_new_draft_starts_from_the_live_advert() -> None:
    """Editing one field must not silently blank the rest."""
    from app.jd_versions import save_draft

    db = _db(draft_row=None)
    await save_draft(
        db, requisition_id=REQ, company_id=COMPANY, actor_user_id=ACTOR,
        content={"jd_text": "Only the prose changed."},
    )
    insert = next(
        c for c in db.execute.await_args_list
        if "INSERT INTO jd_versions" in c.args[0].text
    )
    params = insert.args[1]
    assert params["jd_text"] == "Only the prose changed."
    # The skills came from the live advert, not from nowhere.
    assert "Python" in params["required_skills"]


# ===========================================================================
# An empty opening gets no history
# ===========================================================================
@pytest.mark.asyncio
async def test_an_opening_with_no_advert_mints_no_version() -> None:
    """Inventing an empty v1 would put a row in a history that never happened."""
    from app.jd_versions import record_edit

    db = _db(live={"jd_text": None, "responsibilities": [], "required_skills": [],
                   "nice_to_have_skills": []})
    assert await record_edit(
        db, requisition_id=REQ, company_id=COMPANY, actor_user_id=ACTOR
    ) is None


def test_the_backfill_skips_openings_with_no_advert() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "jsonb_array_length" in sql
    assert "btrim(r.jd_text)" in sql


def test_the_backfill_does_not_claim_the_jd_was_written_today() -> None:
    """A false provenance record in the table that exists to provide
    provenance."""
    sql = MIGRATION.read_text(encoding="utf-8")
    backfill = sql.split("INSERT INTO jd_versions")[1]
    assert "r.created_at" in backfill
    assert "now()" not in backfill


# ===========================================================================
# The guardrail: a JD edit does not re-grade anybody
# ===========================================================================
def test_nothing_in_the_jd_module_writes_round_criteria() -> None:
    """Group C froze a published workflow's competencies at authoring time so
    that editing the advert could not retrospectively change what a candidate
    was measured against. Both PH3-B3 and PH3-B6 name this as a guardrail."""
    source = (APP / "jd_versions.py").read_text(encoding="utf-8")
    body = source.split('"""', 2)[-1]  # skip the module docstring, which names it
    assert "round_criteria" not in body


def test_the_jd_routes_do_not_write_round_criteria() -> None:
    from app.routers import hr_requisitions

    for name in (
        "put_jd_draft", "delete_jd_draft", "publish_jd_version",
        "get_jd_history", "get_jd_draft", "update_requisition",
    ):
        src = inspect.getsource(getattr(hr_requisitions, name))
        assert "round_criteria" not in src, name
        assert "set_round_criteria" not in src, name


def test_the_migration_does_not_touch_round_criteria() -> None:
    assert "round_criteria" not in MIGRATION.read_text(encoding="utf-8").split('"""')[2]


# ===========================================================================
# Compatibility: the existing edit path still publishes
# ===========================================================================
def test_editing_through_the_existing_patch_still_takes_effect_immediately() -> None:
    """A dozen screens use PATCH /requisitions/{id}. "Editing a JD quietly
    stopped taking effect" would be a worse regression than the absence of a
    draft step, which is why record_edit publishes."""
    from app.jd_versions import record_edit

    src = inspect.getsource(record_edit)
    assert "'published'" in src
    assert "published_jd_version_id" in src


def test_the_patch_records_the_version_after_writing_not_before() -> None:
    """The patch is partial; reconstructing "what the advert now says" from the
    changed fields alone would store a version that never existed."""
    from app.routers.hr_requisitions import update_requisition

    src = inspect.getsource(update_requisition)
    assert src.index("UPDATE job_requisitions SET") < src.index("record_edit")


def test_record_edit_reads_the_row_rather_than_taking_content() -> None:
    from app.jd_versions import record_edit

    assert "content" not in inspect.signature(record_edit).parameters


# ===========================================================================
# Provenance
# ===========================================================================
def test_a_version_records_who_wrote_it_and_when_it_was_live() -> None:
    from app.routers.hr_requisitions import JdVersionOut

    for field in ("created_by_user_id", "created_by_name", "created_at",
                  "published_at", "superseded_at", "version", "status"):
        assert field in JdVersionOut.model_fields, field


def test_the_history_says_which_version_is_currently_published() -> None:
    from app.routers.hr_requisitions import JdHistoryOut

    assert "published_version_id" in JdHistoryOut.model_fields


def test_history_is_newest_first() -> None:
    from app.jd_versions import list_versions

    assert "ORDER BY v.version DESC" in inspect.getsource(list_versions)


def test_a_discarded_draft_leaves_its_number_unused() -> None:
    from app.jd_versions import discard_draft

    src = inspect.getsource(discard_draft)
    assert "DELETE FROM jd_versions" in src
    # No renumbering anywhere.
    assert "version =" not in src


def test_publishing_and_drafting_are_audited() -> None:
    from app.routers import hr_requisitions

    for name, action in (
        ("put_jd_draft", "requisition.jd.draft_saved"),
        ("delete_jd_draft", "requisition.jd.draft_discarded"),
        ("publish_jd_version", "requisition.jd.published"),
    ):
        assert action in inspect.getsource(getattr(hr_requisitions, name)), name


def test_list_items_are_bounded_because_they_render_on_a_public_page() -> None:
    from app.routers.hr_requisitions import JdContentIn

    with pytest.raises(ValueError, match="60 items"):
        JdContentIn(required_skills=[f"skill-{i}" for i in range(61)])
    trimmed = JdContentIn(required_skills=["  Python   3  ", "", "   "])
    assert trimmed.required_skills == ["Python 3"]


def test_timestamps_serialise_as_iso_strings() -> None:
    from app.routers.hr_requisitions import _version_out

    out = _version_out({
        "id": uuid.uuid4(), "version": 1, "status": "published",
        "created_at": datetime(2026, 9, 16, 9, 0, tzinfo=UTC),
        "published_at": datetime(2026, 9, 16, 9, 0, tzinfo=UTC),
        "superseded_at": None, **LIVE,
    })
    assert out.created_at.startswith("2026-09-16")
    assert out.superseded_at is None
