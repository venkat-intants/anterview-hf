"""Unit tests for HR applicant screening — focus on the tenant-isolation and
validation logic. DB is mocked; the full upload→score→rank flow + cross-tenant
isolation are covered by live end-to-end verification.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from shared.auth.base import User


@pytest.mark.asyncio
async def test_get_hr_company_no_company_403() -> None:
    from app.routers.hr_applicants import get_hr_company

    db = AsyncMock()
    db.scalar = AsyncMock(return_value=None)  # HR has no company_id
    user = User(user_id=str(uuid.uuid4()), full_name="", email="", roles=["hr_manager"])
    with pytest.raises(HTTPException) as exc:
        await get_hr_company(user, db)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_get_hr_company_returns_uid_and_company() -> None:
    from app.routers.hr_applicants import get_hr_company

    uid, cid = uuid.uuid4(), uuid.uuid4()
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=cid)
    user = User(user_id=str(uid), full_name="", email="", roles=["hr_manager"])
    got_uid, got_cid = await get_hr_company(user, db)
    assert got_uid == uid
    assert got_cid == cid


@pytest.mark.asyncio
async def test_get_owned_404_when_not_in_company() -> None:
    """Cross-tenant / missing applicant -> 404 (the isolation boundary)."""
    from app.routers.hr_applicants import _get_owned

    db = AsyncMock()
    db.scalar = AsyncMock(return_value=None)
    with pytest.raises(HTTPException) as exc:
        await _get_owned(db, uuid.uuid4(), uuid.uuid4())
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_update_status_rejects_invalid_value() -> None:
    from app.routers.hr_applicants import StatusUpdate, update_applicant_status

    db = AsyncMock()
    with pytest.raises(HTTPException) as exc:
        await update_applicant_status(
            uuid.uuid4(), StatusUpdate(status="bogus"), (uuid.uuid4(), uuid.uuid4()), db
        )
    assert exc.value.status_code == 400


def test_name_from_filename_humanizes() -> None:
    from app.routers.hr_applicants import _name_from_filename

    assert _name_from_filename("Jane_Doe_Resume.pdf") == "Jane Doe Resume"
    assert _name_from_filename("C:/tmp/john-smith.PDF") == "john smith"
    assert _name_from_filename("résumé.pdf") == "résumé"
    assert _name_from_filename(".pdf") == "Unnamed candidate"
    assert len(_name_from_filename("x" * 500 + ".pdf")) <= 200


@pytest.mark.asyncio
async def test_bulk_upload_rejects_oversized_batch() -> None:
    """More than the per-batch cap -> 400 before any file is touched."""
    from app.routers.hr_applicants import _MAX_BULK_FILES, bulk_upload_applicants

    db = AsyncMock()
    too_many = [object()] * (_MAX_BULK_FILES + 1)
    with pytest.raises(HTTPException) as exc:
        await bulk_upload_applicants(
            files=too_many,  # type: ignore[arg-type]
            target_job_title="Engineer",
            ctx=(uuid.uuid4(), uuid.uuid4()),
            db=db,
        )
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_bulk_upload_empty_batch_400() -> None:
    from app.routers.hr_applicants import bulk_upload_applicants

    db = AsyncMock()
    with pytest.raises(HTTPException) as exc:
        await bulk_upload_applicants(
            files=[], target_job_title="Engineer", ctx=(uuid.uuid4(), uuid.uuid4()), db=db
        )
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_list_applicants_is_paged_and_leaves_resume_text_in_the_database() -> None:
    """The list used to SELECT every applicant row, resume text and all.

    A company with thousands of uploaded CVs turned one page load into tens of
    megabytes of ORM objects held for the length of the scan.
    """
    from app.routers.hr_applicants import list_applicants

    result = MagicMock()
    result.scalars.return_value.all.return_value = []
    db = AsyncMock()
    db.execute = AsyncMock(return_value=result)

    await list_applicants(ctx=(uuid.uuid4(), uuid.uuid4()), db=db, page=3, per_page=25)

    stmt = db.execute.await_args.args[0]
    compiled = str(stmt.compile(compile_kwargs={"literal_binds": True}))
    assert "LIMIT 25" in compiled
    assert "OFFSET 50" in compiled
    assert "resume_text" not in compiled
    assert "target_jd_text" not in compiled


@pytest.mark.asyncio
async def test_list_applicants_defaults_to_a_bounded_page() -> None:
    from app.routers.hr_applicants import _LIST_PAGE_SIZE, list_applicants

    result = MagicMock()
    result.scalars.return_value.all.return_value = []
    db = AsyncMock()
    db.execute = AsyncMock(return_value=result)

    await list_applicants(ctx=(uuid.uuid4(), uuid.uuid4()), db=db)

    compiled = str(
        db.execute.await_args.args[0].compile(compile_kwargs={"literal_binds": True})
    )
    assert f"LIMIT {_LIST_PAGE_SIZE}" in compiled


@pytest.mark.asyncio
async def test_search_results_are_paged_too(monkeypatch: pytest.MonkeyPatch) -> None:
    """?q= is capped at _SEARCH_LIMIT, but page=2 must not repeat page one."""
    from app.routers import hr_applicants as mod

    ranked = [MagicMock(name=f"applicant-{i}") for i in range(7)]

    async def _fake_search(*_args: object, **_kwargs: object) -> list[object]:
        return ranked

    monkeypatch.setattr(mod, "_semantic_search", _fake_search)
    out = await mod.list_applicants(
        ctx=(uuid.uuid4(), uuid.uuid4()), db=AsyncMock(), q="welder", page=2, per_page=3
    )
    assert out == ranked[3:6]


def test_apply_score_maps_fields() -> None:
    from app.models import Applicant
    from app.routers.hr_applicants import _apply_score

    a = Applicant()
    _apply_score(
        a,
        {
            "overall": 77,
            "breakdown": {"skills_match": 80, "experience_relevance": 70},
            "strengths": ["x"],
            "concerns": ["y"],
            "recommendation": "strong_fit",
            "summary": "Strong.",
        },
    )
    assert a.ats_overall == 77
    assert a.ats_recommendation == "strong_fit"
    assert a.ats_breakdown == {"skills_match": 80, "experience_relevance": 70}


# ---------------------------------------------------------------------------
# LIKE wildcard escaping (2026-08-06)
#
# Both applicant-search paths interpolated the caller's `job` filter straight
# into a LIKE pattern. Not injection — the value is bound as a parameter — but
# `%` and `_` are wildcards inside the pattern, so `job=%` matched every
# applicant in the tenant and `job=_` matched any single character. The filter
# could widen its own result set.
# ---------------------------------------------------------------------------


def test_like_literal_escapes_wildcards() -> None:
    from app.routers.hr_applicants import _like_literal

    assert _like_literal("%") == chr(92) + "%"
    assert _like_literal("_") == chr(92) + "_"
    assert _like_literal("100%_bonus") == "100" + chr(92) + "%" + chr(92) + "_bonus"


def test_like_literal_escapes_the_escape_character_first() -> None:
    """Ordering matters: escaping % before the backslash would re-escape the
    backslash this function just introduced, turning one literal into two."""
    from app.routers.hr_applicants import _like_literal

    assert _like_literal(chr(92)) == chr(92) * 2
    # A backslash followed by a percent must yield escaped-backslash + escaped-%
    assert _like_literal(chr(92) + "%") == chr(92) * 2 + chr(92) + "%"


def test_like_literal_leaves_ordinary_titles_untouched() -> None:
    """The common case must not change — this filter is used on every list call."""
    from app.routers.hr_applicants import _like_literal

    for title in ("Data Engineer", "Sr. QA (Manual)", "Nurse - ICU", "Welder/Fitter"):
        assert _like_literal(title) == title


@pytest.mark.asyncio
async def test_semantic_search_binds_the_escaped_job_pattern() -> None:
    """The raw-SQL path must actually CALL the escaper, not merely have one.

    Testing _like_literal alone passes even if the call site still interpolates
    the raw value — which is exactly the bug. This asserts on the parameter the
    query is executed with.
    """
    import uuid as _uuid
    from unittest.mock import AsyncMock, patch

    from app.routers import hr_applicants

    captured: dict[str, object] = {}

    class _Result:
        @staticmethod
        def all() -> list[object]:
            return []

        @staticmethod
        def fetchall() -> list[object]:
            return []

    class _Db:
        async def execute(self, stmt: object, params: dict[str, object]) -> _Result:
            captured.update(params)
            captured["sql"] = str(stmt)
            return _Result()

    with patch.object(
        hr_applicants, "embed_one_remote", AsyncMock(side_effect=Exception("no embedder"))
    ), patch.object(hr_applicants, "EmbeddingError", Exception):
        await hr_applicants._semantic_search(
            db=_Db(),  # type: ignore[arg-type]
            hr_uid=_uuid.uuid4(),
            company_id=_uuid.uuid4(),
            q="welder",
            status_filter=None,
            job="100%_bonus",
        )

    assert captured["job"] == "%100" + chr(92) + "%" + chr(92) + "_bonus%", (
        f"job filter reached SQL unescaped: {captured['job']!r}"
    )
    assert "ESCAPE" in str(captured["sql"]), (
        "the ILIKE must declare its escape character explicitly rather than "
        "relying on the server's standard_conforming_strings default"
    )


@pytest.mark.asyncio
async def test_list_applicants_orm_path_escapes_the_job_pattern() -> None:
    """Same guarantee for the ORM leg, which builds its own ILIKE.

    Exercises list_applicants and inspects the statement it actually executes.
    Building the clause here instead would test SQLAlchemy, not our call site —
    and a call site that dropped the escaping would still pass.
    """
    import uuid as _uuid

    from app.routers.hr_applicants import list_applicants

    captured: dict[str, str] = {}

    class _Scalars:
        @staticmethod
        def all() -> list[object]:
            return []

    class _Result:
        @staticmethod
        def scalars() -> _Scalars:
            return _Scalars()

    class _Db:
        async def execute(self, stmt: object) -> _Result:
            captured["sql"] = str(
                stmt.compile(compile_kwargs={"literal_binds": True})  # type: ignore[attr-defined]
            )
            return _Result()

    await list_applicants(
        ctx=(_uuid.uuid4(), _uuid.uuid4()),
        db=_Db(),  # type: ignore[arg-type]
        status_filter=None,
        q=None,
        job="100%_bonus",
    )

    sql = captured["sql"]
    assert "ESCAPE" in sql.upper(), f"ORM ILIKE lost its escape clause: {sql}"
    assert chr(92) + "%" in sql, f"job filter reached SQL unescaped: {sql}"
