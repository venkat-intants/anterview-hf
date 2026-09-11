"""Unit tests for B1/B4/B5 step 1 — every applicant filed under an opening, and
every ATS score written to the application it belongs to.

The SQL is exercised end to end by
``tests/integration/smoke_group_b_enrolments_everywhere.py``; these pin the
decisions: where a score lands, when the applicant row mirrors it, which
opening an upload is filed under, and who the ledger says did it.
"""

from __future__ import annotations

import inspect
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

SCORE = {"overall": 7, "breakdown": {"skills": 7}, "strengths": ["a"], "concerns": ["b"],
         "recommendation": "consider", "summary": "ok"}


def _db() -> AsyncMock:
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=None)
    db.execute = AsyncMock()
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    return db


# ===========================================================================
# apply_ats_to_enrolment — the score belongs to the application
# ===========================================================================
@pytest.mark.asyncio
@pytest.mark.parametrize(("newer", "latest"), [(None, True), (1, False)])
async def test_a_score_is_written_to_its_enrolment_with_the_cv_that_produced_it(
    newer: object, latest: bool,
) -> None:
    from app.applicant_enrichment import apply_ats_to_enrolment

    db = _db()
    db.execute.return_value = MagicMock(first=MagicMock(return_value=(uuid.uuid4(), "t")))
    db.scalar.return_value = newer
    eid = uuid.uuid4()

    got = await apply_ats_to_enrolment(db, enrolment_id=eid, score=SCORE,
                                       resume_key="applicants/c/a.pdf")

    sql, params = db.execute.call_args.args
    assert "UPDATE enrolments SET ats_overall" in str(sql)
    assert "scored_resume_s3_key = :k" in str(sql) and "scored_at = :n" in str(sql)
    assert params["k"] == "applicants/c/a.pdf" and params["e"] == eid
    assert params["o"] == 7
    # Mirrored onto the applicant only when this is their latest application.
    assert got is latest


@pytest.mark.asyncio
async def test_a_missing_enrolment_mirrors_nothing() -> None:
    from app.applicant_enrichment import apply_ats_to_enrolment

    db = _db()
    db.execute.return_value = MagicMock(first=MagicMock(return_value=None))
    assert await apply_ats_to_enrolment(
        db, enrolment_id=uuid.uuid4(), score=SCORE, resume_key=None
    ) is False


# ===========================================================================
# requisition_for_title — the opening a typed title belongs to
# ===========================================================================
@pytest.mark.asyncio
async def test_a_typed_title_finds_its_existing_opening() -> None:
    from app.requisitions import requisition_for_title

    rid = uuid.uuid4()
    db = _db()
    db.execute.return_value = MagicMock(mappings=MagicMock(return_value=MagicMock(
        first=MagicMock(return_value={"id": rid, "title": "Staff Nurse", "level": "mid",
                                      "jd_text": None}))))

    got = await requisition_for_title(db, company_id=uuid.uuid4(), title="  STAFF   nurse ",
                                      level="mid", jd_text=None, actor_user_id=None)

    assert got["id"] == rid and got["created"] is False
    params = db.execute.call_args.args[1]
    assert params["k"] == "staff nurse"  # normalise_title — case and spacing only
    assert db.execute.await_count == 1, "found it, so nothing was inserted"


@pytest.mark.asyncio
async def test_a_new_title_mints_an_opening_flagged_for_review() -> None:
    """Nobody chose to open it — a typed title implied it — so it goes to the
    review screen, exactly like the backfill's own guesses."""
    from app.requisitions import requisition_for_title

    db = _db()
    db.execute.return_value = MagicMock(mappings=MagicMock(return_value=MagicMock(
        first=MagicMock(return_value=None))))
    nested = MagicMock()
    nested.__aenter__ = AsyncMock(return_value=None)
    nested.__aexit__ = AsyncMock(return_value=False)
    db.begin_nested = MagicMock(return_value=nested)

    got = await requisition_for_title(db, company_id=uuid.uuid4(), title="Welder",
                                      level="entry", jd_text=None, actor_user_id=uuid.uuid4())

    insert = str(db.execute.call_args_list[-1].args[0])
    assert "INSERT INTO job_requisitions" in insert
    assert "'open',true" in insert.replace(" ", "")  # status open, from_backfill true
    assert got["created"] is True and got["title"] == "Welder"


@pytest.mark.asyncio
async def test_a_blank_title_cannot_be_filed() -> None:
    from app.requisitions import requisition_for_title

    with pytest.raises(ValueError, match="job title is required"):
        await requisition_for_title(_db(), company_id=uuid.uuid4(), title="   ",
                                    level="mid", jd_text=None, actor_user_id=None)


def test_the_title_lookup_uses_the_same_rule_as_the_unique_index() -> None:
    from app.requisitions import _FIND_BY_TITLE_SQL

    assert "lower(btrim(regexp_replace(title, '\\s+', ' ', 'g'))) = :k" in _FIND_BY_TITLE_SQL
    assert "deleted_at IS NULL" in _FIND_BY_TITLE_SQL


# ===========================================================================
# enrol_applicant — who the ledger says did it
# ===========================================================================
@pytest.mark.asyncio
@pytest.mark.parametrize(("actor", "automated"), [(uuid.uuid4(), False), (None, True)])
async def test_the_ledger_says_whether_a_person_filed_the_applicant(
    monkeypatch: pytest.MonkeyPatch, actor: uuid.UUID | None, automated: bool,
) -> None:
    import app.workflow_runner as wr

    async def _no_workflow(*_: object) -> None:
        return None

    monkeypatch.setattr(wr, "published_workflow", _no_workflow)
    db = _db()
    await wr.enrol_applicant(db, company_id=uuid.uuid4(), applicant_id=uuid.uuid4(),
                             requisition_id=uuid.uuid4(), target_job_title="Nurse",
                             actor_user_id=actor, reason="added by HR upload")

    ledger = next(c.args[1] for c in db.execute.call_args_list
                  if "INSERT INTO stage_transitions" in str(c.args[0]))
    assert ledger["auto"] is automated and ledger["a"] == actor
    assert ledger["r"] == "added by HR upload"


# ===========================================================================
# The reconciler scores applications, not applicants
# ===========================================================================
def test_an_application_is_scored_against_its_own_opening() -> None:
    """Scoring against the applicant's target scored every older application
    against the newest job the person applied for."""
    from app.reconciliation import _UNSCORED_WORK_SQL

    sql = _UNSCORED_WORK_SQL
    assert "ELSE e.target_job_title END AS job_title" in sql
    assert "e.ats_overall IS NULL" in sql
    assert "rs.kind = :kind_enr AND rs.ref_id = e.id" in sql
    # Applicants with no enrolment at all still get scored the old way.
    assert "e.id IS NULL AND a.ats_overall IS NULL" in sql


def _work(eid: uuid.UUID | None) -> MagicMock:
    row = {"applicant_id": uuid.uuid4(), "enrolment_id": eid, "job_title": "Welder",
           "level": "entry", "jd_text": "Welding"}
    return MagicMock(mappings=MagicMock(return_value=MagicMock(
        all=MagicMock(return_value=[row]))))


@pytest.mark.asyncio
@pytest.mark.parametrize("latest", [True, False])
async def test_the_reconciler_writes_the_enrolment_and_mirrors_only_the_latest(
    monkeypatch: pytest.MonkeyPatch, latest: bool,
) -> None:
    import app.reconciliation as rec

    eid = uuid.uuid4()
    db = _db()
    db.execute.return_value = _work(eid)
    applicant = MagicMock(resume_text="cv", resume_s3_key="applicants/c/a.pdf",
                          upload_batch_id=None, created_by_user_id=None, id=uuid.uuid4())
    db.get = AsyncMock(return_value=applicant)
    titles: list[str] = []
    written: list[dict] = []
    mirrored: list[object] = []

    async def _score(**kw: object) -> dict:
        titles.append(str(kw["job_title"]))
        return SCORE

    async def _to_enrolment(_db: object, **kw: object) -> bool:
        written.append(kw)
        return latest

    monkeypatch.setattr(rec, "score_resume_remote", _score)
    monkeypatch.setattr(rec, "apply_ats_to_enrolment", _to_enrolment)
    monkeypatch.setattr(rec, "apply_ats_score", lambda a, _s: mirrored.append(a))
    monkeypatch.setattr(rec, "apply_extracted_identity", lambda _a, _s: False)

    result = rec.PassResult()
    await rec._score_pass(db, result)

    assert titles == ["Welder"], "scored against the enrolment's role"
    assert written[0]["enrolment_id"] == eid
    assert written[0]["resume_key"] == "applicants/c/a.pdf"
    assert mirrored == ([applicant] if latest else [])
    assert result.scored == 1


@pytest.mark.asyncio
async def test_an_enrolment_scoring_failure_backs_off_that_enrolment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.reconciliation as rec

    eid = uuid.uuid4()
    db = _db()
    db.execute.return_value = _work(eid)
    db.get = AsyncMock(return_value=MagicMock(resume_text="cv", upload_batch_id=None,
                                              created_by_user_id=None))
    seen: list[tuple] = []

    async def _down(**_: object) -> dict:
        raise RuntimeError("scorer down")

    async def _fail(_db: object, kind: str, ref: object, _e: str, **_: object) -> bool:
        seen.append((kind, ref))
        return False

    monkeypatch.setattr(rec, "score_resume_remote", _down)
    monkeypatch.setattr(rec, "_record_failure", _fail)
    await rec._score_pass(db, rec.PassResult())
    assert seen == [(rec.KIND_ENROLMENT_ATS, eid)]


# ===========================================================================
# HR uploads file every applicant under an opening
# ===========================================================================
def test_a_single_upload_is_filed_under_an_opening_before_it_commits() -> None:
    from app.routers.hr_applicants import create_applicant

    src = inspect.getsource(create_applicant)
    # A bad file is rejected before anything can mint an opening.
    assert src.index("_extract_pdf_text(raw)") < src.index("await _resolve_opening(")
    # Applicant and enrolment land in one transaction.
    filed = src.index("await _file_under(")
    assert filed < src.index("await db.commit()", filed)
    # And the score goes to the enrolment too.
    assert "apply_ats_to_enrolment(" in src


def test_a_bulk_upload_files_every_row_under_one_opening() -> None:
    from app.routers.hr_applicants import _ingest_resume, bulk_upload_applicants

    bulk = inspect.getsource(bulk_upload_applicants)
    # The opening is committed before the per-file loop, so one bad file's
    # rollback cannot take it with it.
    assert bulk.index("await _resolve_opening(") < bulk.index("await db.commit()")
    assert bulk.index("await db.commit()") < bulk.index("for f in files:")
    assert "opening=opening" in bulk
    ingest = inspect.getsource(_ingest_resume)
    assert ingest.index("await _file_under(") < ingest.index("await db.commit()")


def test_rescoring_scores_an_application_against_its_own_role() -> None:
    from app.routers.hr_applicants import rescore_applicant

    src = inspect.getsource(rescore_applicant)
    assert 'job_title=enr["target_job_title"] if enr else a.target_job_title' in src
    assert "apply_ats_to_enrolment(" in src
    assert "if latest:" in src


# ===========================================================================
# A returning candidate's new CV does not overwrite the one that was scored
# ===========================================================================
def test_a_returning_candidates_cv_gets_its_own_key() -> None:
    from app.routers.public_apply import submit_application

    src = inspect.getsource(submit_application)
    assert 'f"applicants/{company_id}/{applicant_id}-{uuid.uuid4().hex[:12]}.pdf"' in src
    # The replaced CV is removed only if no application was scored against it.
    assert "WHERE scored_resume_s3_key = :k" in src
    assert "if still_used is None:" in src
