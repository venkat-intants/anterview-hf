"""Group E foundations — who is awaiting a person, since when, and on which CV.

Every Group E screen reads these three things, and each had more than one
answer: "awaiting a decision" was defined three ways, wait times were measured
from ``updated_at`` (which a rescore resets), and an application still waiting
to be scored had no record of the CV it was submitted with. The database half is
exercised against a real Postgres in ``smoke_group_e_foundations``.
"""

from __future__ import annotations

import inspect
import uuid
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

SCORE = {"overall": 7, "breakdown": {"skills": 7}, "strengths": ["x"], "concerns": ["y"],
         "recommendation": "consider", "summary": "ok"}


def _db(rows: list[dict[str, Any]]) -> AsyncMock:
    db = AsyncMock()
    result = MagicMock()
    result.mappings.return_value.all.return_value = rows
    db.execute = AsyncMock(return_value=result)
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    return db


def _squash(sql: object) -> str:
    return " ".join(str(sql).split())


# ===========================================================================
# One definition of "awaiting a decision"
# ===========================================================================
def test_the_decision_queue_uses_the_shared_definition() -> None:
    from app.workflow_runner import decision_queue

    src = _squash(inspect.getsource(decision_queue))
    assert "enrolment_awaits_human(e.status, e.current_round_id)" in src
    # The inline predicate that counted never-shortlisted applicants as finished.
    assert "e.current_round_id IS NULL" not in src


def test_the_watcher_counts_the_same_people_the_queue_shows() -> None:
    from app.agents.watch_runner import OPENING_HEALTH_SQL

    sql = _squash(OPENING_HEALTH_SQL)
    assert sql.count("enrolment_awaits_human(e.status, e.current_round_id)") == 2
    assert "(e.status = 'held' OR e.current_round_id IS NULL)" not in sql


def test_the_function_leaves_out_candidates_nobody_has_started() -> None:
    import pathlib

    migration = next(
        pathlib.Path(__file__).parents[2].glob("alembic/versions/*_a1c3e5f7b9d2_*.py")
    ).read_text(encoding="utf-8")
    after = migration[migration.index("FUNCTION enrolment_awaits_human"):]
    body = _squash(after.split("$$")[1])  # between the opening and closing $$
    assert "p_status = 'held'" in body
    assert "(p_round_id IS NULL AND p_status = 'interviewed')" in body
    assert "wr.kind = 'human_review'" in body
    assert "'new'" not in body and "'shortlisted'" not in body


def _row(**over: Any) -> dict[str, Any]:
    return {"id": uuid.uuid4(), "title": "Python Developer", "level": "mid", "status": "open",
            "created_at": datetime.now(tz=UTC), "closes_at": None, "from_backfill": False,
            **over}


def test_awaiting_and_unresolved_are_different_numbers() -> None:
    """Everyone mid-round used to show as "awaiting your decision"."""
    from app.routers.hr_requisitions import _to_out

    counts = {"new": 4, "shortlisted": 3, "held": 2, "interviewed": 1, "hired": 1,
              "rejected": 5}
    out = _to_out(_row(), counts, awaiting=3)
    assert out.awaiting_decision == 3
    assert out.unresolved == 10
    assert out.total_enrolments == 16


def test_closing_accounts_for_everyone_undecided_not_only_the_queue() -> None:
    from app.routers.hr_requisitions import set_requisition_status

    src = inspect.getsource(set_requisition_status)
    assert ").unresolved" in src
    assert ").awaiting_decision" not in src


# ===========================================================================
# Time from the ledger, not updated_at
# ===========================================================================
def test_the_watcher_measures_the_wait_from_the_ledger() -> None:
    from app.agents.watch_runner import OPENING_HEALTH_SQL

    sql = _squash(OPENING_HEALTH_SQL)
    assert "enrolment_state_since(e.id, e.created_at)" in sql
    assert "NOW() - e.updated_at" not in sql


def test_the_dashboard_median_comes_from_the_ledger() -> None:
    from app.routers.hr_requisitions import requisition_dashboard

    src = _squash(inspect.getsource(requisition_dashboard))
    assert "enrolment_state_since(e.id, e.created_at)" in src
    assert "NOW() - e.updated_at" not in src


# ===========================================================================
# Each application keeps the CV it was submitted with
# ===========================================================================
@pytest.mark.asyncio
async def test_enrolling_pins_the_submitted_cv(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.workflow_runner as runner

    async def _no_workflow(*_: object, **__: object) -> None:
        return None

    monkeypatch.setattr(runner, "published_workflow", _no_workflow)
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=None)
    await runner.enrol_applicant(
        db, company_id=uuid.uuid4(), applicant_id=uuid.uuid4(), requisition_id=uuid.uuid4(),
        target_job_title="Welder", resume_s3_key="applicants/c/a-1.pdf",
    )
    insert = next(c for c in db.execute.call_args_list
                  if "INSERT INTO enrolments" in str(c.args[0]))
    assert "applied_resume_s3_key" in str(insert.args[0])
    assert insert.args[1]["k"] == "applicants/c/a-1.pdf"


def test_every_intake_path_pins_the_cv() -> None:
    from app.routers import hr_applicants, public_apply

    assert "resume_s3_key=s3_key," in inspect.getsource(public_apply.submit_application)
    assert "resume_s3_key=applicant.resume_s3_key," in inspect.getsource(hr_applicants._file_under)


def test_a_replaced_cv_is_kept_while_an_unscored_application_needs_it() -> None:
    """Only the scored key was checked, so the file an unscored earlier
    application still needed was deleted as unreferenced."""
    from app.routers import hr_applicants, public_apply

    for module in (hr_applicants, public_apply):
        # The SQL is split over two string literals; join them before looking.
        src = _squash(inspect.getsource(module).replace('"\n', "").replace('"', ""))
        assert "WHERE scored_resume_s3_key = :k OR applied_resume_s3_key = :k" in src


def _work(eid: uuid.UUID, applied_key: str | None) -> list[dict[str, Any]]:
    return [{"applicant_id": uuid.uuid4(), "enrolment_id": eid, "applied_key": applied_key,
             "job_title": "Welder", "level": "entry", "jd_text": None}]


def _applicant() -> MagicMock:
    return MagicMock(resume_text="NEW cv", resume_s3_key="applicants/c/new.pdf",
                     upload_batch_id=None, created_by_user_id=None, id=uuid.uuid4(),
                     email="a@x.test", pending_enrichment=False)


def _patch_scoring(monkeypatch: pytest.MonkeyPatch, rec: Any) -> tuple[list, list]:
    texts: list[str] = []
    written: list[dict] = []

    async def _score(**kw: object) -> dict:
        texts.append(str(kw["resume_text"]))
        return SCORE

    async def _to_enrolment(_db: object, **kw: object) -> bool:
        written.append(kw)
        return False

    monkeypatch.setattr(rec, "score_resume_remote", _score)
    monkeypatch.setattr(rec, "apply_ats_to_enrolment", _to_enrolment)
    monkeypatch.setattr(rec, "apply_extracted_identity", lambda _a, _s, **_: False)
    return texts, written


@pytest.mark.asyncio
async def test_an_older_application_is_scored_against_the_cv_it_was_sent_with(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.reconciliation as rec
    import app.routers.resume as resume

    eid = uuid.uuid4()
    db = _db(_work(eid, "applicants/c/old.pdf"))
    db.get = AsyncMock(return_value=_applicant())
    downloaded: list[str] = []

    async def _download(key: str) -> bytes:
        downloaded.append(key)
        return b"%PDF old"

    async def _extract(_raw: bytes) -> str:
        return "OLD cv"

    monkeypatch.setattr(resume, "_download_from_s3", _download)
    monkeypatch.setattr(resume, "_extract_pdf_text", _extract)
    texts, written = _patch_scoring(monkeypatch, rec)

    result = rec.PassResult()
    await rec._score_pass(db, result)

    assert downloaded == ["applicants/c/old.pdf"]
    assert texts == ["OLD cv"], "scored against the newer CV"
    assert written[0]["resume_key"] == "applicants/c/old.pdf"
    assert result.scored == 1


@pytest.mark.asyncio
async def test_the_current_cv_needs_no_download(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.reconciliation as rec
    import app.routers.resume as resume

    async def _never(_key: str) -> bytes:
        raise AssertionError("downloaded a CV that is already on the row")

    monkeypatch.setattr(resume, "_download_from_s3", _never)
    db = _db(_work(uuid.uuid4(), "applicants/c/new.pdf"))
    db.get = AsyncMock(return_value=_applicant())
    texts, written = _patch_scoring(monkeypatch, rec)

    await rec._score_pass(db, rec.PassResult())
    assert texts == ["NEW cv"]
    assert written[0]["resume_key"] == "applicants/c/new.pdf"


@pytest.mark.asyncio
async def test_an_unreadable_submitted_cv_is_a_retry_not_a_score(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.reconciliation as rec
    import app.routers.resume as resume

    async def _gone(_key: str) -> bytes:
        raise RuntimeError("object missing")

    monkeypatch.setattr(resume, "_download_from_s3", _gone)
    eid = uuid.uuid4()
    db = _db(_work(eid, "applicants/c/old.pdf"))
    db.get = AsyncMock(return_value=_applicant())
    texts, _written = _patch_scoring(monkeypatch, rec)
    seen: list[tuple] = []

    async def _fail(_db: object, kind: str, ref: object, _e: str, **_: object) -> bool:
        seen.append((kind, ref))
        return False

    monkeypatch.setattr(rec, "_record_failure", _fail)
    result = rec.PassResult()
    await rec._score_pass(db, result)
    assert texts == []
    assert seen == [(rec.KIND_ENROLMENT_ATS, eid)]
    assert result.failed == 1


# ===========================================================================
# Scoring switched off no longer means "being read" forever
# ===========================================================================
def test_rows_nothing_will_score_are_settled_after_the_score_pass() -> None:
    import app.reconciliation as rec

    src = inspect.getsource(rec.reconcile_once) if hasattr(rec, "reconcile_once") else (
        inspect.getsource(rec)
    )
    assert src.index('("score", _score_pass)') < src.index('("settle", _settle_pass)')
    sql = _squash(rec._SETTLE_UNSCORABLE_SQL)
    assert "SET pending_enrichment = false" in sql
    # An application with no workflow yet may still be scored once one publishes.
    assert "(w.id IS NULL OR w.auto_score_on_apply)" in sql


@pytest.mark.asyncio
async def test_settling_a_row_can_finish_its_bulk_upload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.reconciliation as rec

    batch, uploader, aid = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    db = _db([{"id": aid, "upload_batch_id": batch, "created_by_user_id": uploader}])
    announced: list[tuple] = []

    async def _announce(_db: object, _result: object, a: object, b: object, u: object) -> None:
        announced.append((a, b, u))

    monkeypatch.setattr(rec, "_announce_batch", _announce)
    await rec._settle_pass(db, rec.PassResult())
    db.commit.assert_awaited()
    assert announced == [(aid, batch, uploader)]


@pytest.mark.asyncio
async def test_nothing_to_settle_commits_nothing() -> None:
    import app.reconciliation as rec

    db = _db([])
    await rec._settle_pass(db, rec.PassResult())
    db.commit.assert_not_awaited()
