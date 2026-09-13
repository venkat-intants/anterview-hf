"""E5 — bulk resume uploads processed in the background.

The request stores and queues; the reconciler reads, files and scores. These
pin the shape of both halves. ``smoke_group_e_bulk`` runs them against Postgres.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

PDF = b"%PDF-1.4 fake"


# ===========================================================================
# The request
# ===========================================================================
def test_the_request_stores_and_queues_but_reads_nothing() -> None:
    from app.routers.hr_applicants import bulk_upload_applicants

    src = inspect.getsource(bulk_upload_applicants)
    for heavy in ("_extract_pdf_text", "score_resume_remote", "_ingest_resume", "embed_"):
        assert heavy not in src, f"{heavy} runs inside the request again"
    assert "await _upload_to_s3(raw, key)" in src
    assert "await create_batch(" in src
    assert "wake_reconciler()" in src
    assert "status.HTTP_202_ACCEPTED" in src


def test_the_upload_names_a_real_opening() -> None:
    from app.routers.hr_applicants import bulk_upload_applicants

    params = inspect.signature(bulk_upload_applicants).parameters
    assert params["requisition_id"].default is inspect.Parameter.empty
    assert "target_job_title" not in params


def _opening_db(row: dict[str, Any] | None) -> AsyncMock:
    db = AsyncMock()
    result = MagicMock()
    result.mappings.return_value.first.return_value = row
    db.execute = AsyncMock(return_value=result)
    return db


def _upload(name: str, content_type: str, data: bytes) -> MagicMock:
    f = MagicMock()
    f.filename, f.content_type = name, content_type
    f.read = AsyncMock(return_value=data)
    return f


@pytest.mark.asyncio
async def test_another_companys_opening_is_not_found() -> None:
    from app.routers.hr_applicants import bulk_upload_applicants

    with pytest.raises(HTTPException) as exc:
        await bulk_upload_applicants(files=[_upload("a.pdf", "application/pdf", PDF)],
                                     requisition_id=uuid.uuid4(),
                                     ctx=(uuid.uuid4(), uuid.uuid4()), db=_opening_db(None))
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_a_closed_opening_takes_no_uploads() -> None:
    from app.routers.hr_applicants import bulk_upload_applicants

    with pytest.raises(HTTPException) as exc:
        await bulk_upload_applicants(
            files=[_upload("a.pdf", "application/pdf", PDF)], requisition_id=uuid.uuid4(),
            ctx=(uuid.uuid4(), uuid.uuid4()),
            db=_opening_db({"id": uuid.uuid4(), "status": "closed"}),
        )
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_bad_files_are_refused_one_by_one_and_the_rest_are_queued(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.reconciliation as rec
    import app.routers.hr_applicants as hra

    stored: list[str] = []
    batches: list[dict[str, Any]] = []
    woken: list[bool] = []

    async def _store(_raw: bytes, key: str) -> None:
        stored.append(key)

    async def _create(_db: object, **kw: Any) -> None:
        batches.append(kw)

    monkeypatch.setattr(hra, "_upload_to_s3", _store)
    monkeypatch.setattr(hra, "create_batch", _create)
    monkeypatch.setattr(rec, "wake", lambda: woken.append(True))

    company, rid = uuid.uuid4(), uuid.uuid4()
    db = _opening_db({"id": rid, "status": "open"})
    out = await hra.bulk_upload_applicants(
        files=[
            _upload("good.pdf", "application/pdf", PDF),
            _upload("notes.txt", "text/plain", b"hello"),
            _upload("empty.pdf", "application/pdf", b""),
            _upload("huge.pdf", "application/pdf", b"x" * (hra._MAX_RESUME_BYTES + 1)),
        ],
        requisition_id=rid, ctx=(uuid.uuid4(), company), db=db,
    )

    assert out.accepted == 1 and out.failed_count == 3 and out.total_files == 4
    assert {f["error"] for f in out.failed} == {"Not a PDF.", "The file is empty.", "Over 5 MB."}
    assert len(stored) == 1
    assert stored[0].startswith(f"applicants/{company}/uploads/{out.batch_id}/")
    staged = batches[0]["files"]
    assert len(staged) == 4, "every file is recorded, the refused ones as failed"
    assert batches[0]["requisition_id"] == rid
    db.commit.assert_awaited()
    assert woken == [True]


# ===========================================================================
# The background pass
# ===========================================================================
def test_files_are_claimed_row_locked_and_respect_backoff() -> None:
    from app.bulk_ingest import _CLAIM_SQL

    sql = " ".join(_CLAIM_SQL.split())
    assert "FOR UPDATE SKIP LOCKED" in sql
    assert "i.status = 'stored'" in sql
    assert "rs.next_attempt_at > :now" in sql


def test_progress_is_company_scoped() -> None:
    from app.bulk_ingest import _FAILURES_SQL, _ITEMS_SQL, _PROGRESS_SQL

    progress = " ".join(_PROGRESS_SQL.split())
    for clause in ("b.company_id = :c", "i.company_id = b.company_id",
                   "a.company_id = b.company_id", "r.company_id = b.company_id"):
        assert clause in progress, clause
    assert "company_id = :c" in _FAILURES_SQL
    assert "r.company_id = b.company_id" in " ".join(_ITEMS_SQL.split())


def _item() -> dict[str, Any]:
    return {"id": uuid.uuid4(), "batch_id": uuid.uuid4(), "company_id": uuid.uuid4(),
            "filename": "Priya_Sharma_CV.pdf", "s3_key": "applicants/c/uploads/b/i.pdf",
            "requisition_id": uuid.uuid4(), "uploaded_by_user_id": uuid.uuid4(),
            "title": "Support Engineer", "level": "mid", "jd_text": None,
            "requisition_deleted_at": None}


def _pass_db(item: dict[str, Any]) -> AsyncMock:
    db = AsyncMock()
    result = MagicMock()
    result.all.return_value = [(item["id"],)]
    result.mappings.return_value = [item]
    db.execute = AsyncMock(return_value=result)
    return db


@pytest.fixture
def plumbing(monkeypatch: pytest.MonkeyPatch) -> dict[str, list]:
    import app.reconciliation as rec

    seen: dict[str, list] = {"failures": [], "announced": [], "cleared": []}

    async def _announce(_db: object, _r: object, aid: object, b: object, u: object) -> None:
        seen["announced"].append(b)

    async def _clear(_db: object, kind: str, ref: object) -> None:
        seen["cleared"].append((kind, ref))

    monkeypatch.setattr(rec, "_announce_batch", _announce)
    monkeypatch.setattr(rec, "_clear_state", _clear)
    return seen


def _marks(db: AsyncMock) -> list[dict[str, Any]]:
    return [c.args[1] for c in db.execute.call_args_list
            if "UPDATE upload_items" in str(c.args[0]) and "SET status = :s" in str(c.args[0])]


@pytest.mark.asyncio
async def test_a_readable_pdf_becomes_an_applicant(
    monkeypatch: pytest.MonkeyPatch, plumbing: dict[str, list],
) -> None:
    import app.bulk_ingest as bi
    import app.reconciliation as rec
    import app.routers.resume as resume

    item = _item()
    aid, eid = uuid.uuid4(), uuid.uuid4()
    monkeypatch.setattr(resume, "_download_from_s3", AsyncMock(return_value=PDF))
    monkeypatch.setattr(resume, "_extract_pdf_text", AsyncMock(return_value="Priya Sharma"))
    monkeypatch.setattr(bi, "_create_applicant", AsyncMock(return_value=(aid, eid)))

    db = _pass_db(item)
    result = rec.PassResult()
    await bi.ingest_pass(db, result)

    assert result.ingested == 1
    assert _marks(db)[-1]["s"] == "created"
    assert _marks(db)[-1]["a"] == aid and _marks(db)[-1]["en"] == eid
    assert (bi.KIND_UPLOAD_ITEM, item["id"]) in plumbing["cleared"]


@pytest.mark.asyncio
async def test_an_unreadable_pdf_fails_for_good_without_retrying(
    monkeypatch: pytest.MonkeyPatch, plumbing: dict[str, list],
) -> None:
    import app.bulk_ingest as bi
    import app.reconciliation as rec
    import app.routers.resume as resume

    retried = AsyncMock()
    monkeypatch.setattr(rec, "_record_failure", retried)
    monkeypatch.setattr(resume, "_download_from_s3", AsyncMock(return_value=PDF))
    monkeypatch.setattr(resume, "_extract_pdf_text", AsyncMock(side_effect=ValueError("bad")))

    item = _item()
    db = _pass_db(item)
    result = rec.PassResult()
    await bi.ingest_pass(db, result)

    assert _marks(db)[-1] == {**_marks(db)[-1], "s": "failed", "e": bi.UNREADABLE}
    retried.assert_not_awaited()
    assert result.failed == 1
    assert plumbing["announced"] == [item["batch_id"]], "a failed file can finish its batch"


@pytest.mark.asyncio
@pytest.mark.parametrize("gave_up", [False, True])
async def test_a_storage_error_is_retried_then_given_up_on(
    monkeypatch: pytest.MonkeyPatch, plumbing: dict[str, list], gave_up: bool,
) -> None:
    import app.bulk_ingest as bi
    import app.reconciliation as rec
    import app.routers.resume as resume

    attempts: list[tuple] = []

    async def _failure(_db: object, kind: str, ref: object, _e: str, **kw: object) -> bool:
        attempts.append((kind, ref, kw.get("max_attempts")))
        return gave_up

    monkeypatch.setattr(rec, "_record_failure", _failure)
    monkeypatch.setattr(resume, "_download_from_s3", AsyncMock(side_effect=OSError("gone")))

    item = _item()
    db = _pass_db(item)
    result = rec.PassResult()
    await bi.ingest_pass(db, result)

    assert attempts == [(bi.KIND_UPLOAD_ITEM, item["id"], bi.MAX_INGEST_ATTEMPTS)]
    if gave_up:
        assert _marks(db)[-1]["s"] == "failed" and _marks(db)[-1]["e"] == bi.GAVE_UP
        assert result.gave_up == 1
    else:
        assert _marks(db)[-1]["s"] == "stored", "back to the queue for the next attempt"


@pytest.mark.asyncio
async def test_hr_collected_basis_is_recorded_per_applicant_without_pii() -> None:
    from app.bulk_ingest import HR_COLLECTED_CONSENT, record_hr_collected_basis

    db = AsyncMock()
    applicant, uploader = uuid.uuid4(), uuid.uuid4()
    from datetime import UTC, datetime

    await record_hr_collected_basis(
        db, uploader=uploader, applicant_id=applicant, company_id=uuid.uuid4(),
        requisition_id=uuid.uuid4(), batch_id=uuid.uuid4(), now=datetime.now(tz=UTC),
    )
    params = db.execute.call_args.args[1]
    assert params["ct"] == HR_COLLECTED_CONSENT and params["uid"] == uploader
    # One active row per (user, type, purpose): the purpose names the applicant.
    assert params["p"] == f"recruitment:applicant:{applicant}"
    evidence = json.loads(params["ev"])
    assert evidence["source"] == "hr_bulk_upload"
    assert not {"email", "full_name", "filename"} & set(evidence)


# ===========================================================================
# The reconciler
# ===========================================================================
def test_ingestion_runs_before_scoring() -> None:
    import app.reconciliation as rec

    src = inspect.getsource(rec.run_once)
    assert src.index('("ingest", ingest_pass)') < src.index('("score", _score_pass)')


def test_a_batch_is_not_finished_while_its_files_are_queued() -> None:
    from app.reconciliation import _BATCH_COUNTS_SQL

    sql = " ".join(_BATCH_COUNTS_SQL.split())
    assert "i.status IN ('stored', 'processing')" in sql
    assert "apps.outstanding + files.queued AS outstanding" in sql
    assert "apps.unreadable + files.failed AS unreadable" in sql


@pytest.mark.asyncio
async def test_wake_ends_the_wait_early() -> None:
    import app.reconciliation as rec

    rec._wake = None
    waiter = asyncio.create_task(rec._wait(30))
    await asyncio.sleep(0)
    rec.wake()
    await asyncio.wait_for(waiter, timeout=1)


def test_the_loop_drains_only_while_work_is_moving() -> None:
    import app.reconciliation as rec

    busy = rec.PassResult(ingested=5, outstanding={"queued_uploads": 20})
    idle = rec.PassResult(outstanding={"queued_uploads": 20})
    done = rec.PassResult(scored=3, outstanding={"queued_uploads": 0, "unscored": 0})
    assert rec._draining(busy) is True
    assert rec._draining(idle) is False, "no progress: back to the interval, no hot loop"
    assert rec._draining(done) is False
    assert rec._draining(None) is False
