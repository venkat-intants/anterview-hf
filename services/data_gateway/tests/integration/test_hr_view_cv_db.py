"""HR opens a candidate's CV beside the score — real Postgres, storage stubbed.

GET /hr/applicants/{id}/resume: own company only, the application's own CV
when one is named, a PDF only, never cached, every view audited.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from fastapi import HTTPException
from shared.db.engine import build_engine, build_session_factory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.routers import hr_applicants

pytestmark = pytest.mark.integration

PDF = b"%PDF-1.7\n% a CV\n"


@pytest_asyncio.fixture
async def db() -> AsyncIterator[AsyncSession]:
    engine = build_engine(database_url=settings.database_url, database_ssl=settings.database_ssl,
                          pool_size=2)
    factory = build_session_factory(engine)
    try:
        async with factory() as session:
            await session.begin()
            # The route commits its audit row. Stubbed so nothing a test writes
            # survives it: CI's database is shared by every integration test,
            # and count-based ones break on leftover rows. The audit row is
            # flushed to the test's own transaction, never committed.
            session.commit = AsyncMock()  # type: ignore[method-assign]
            try:
                yield session
            finally:
                await session.rollback()
    finally:
        await engine.dispose()


@pytest.fixture
def storage(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    fetched = AsyncMock(return_value=PDF)
    monkeypatch.setattr(hr_applicants, "_download_from_s3", fetched)
    return fetched


def _request() -> MagicMock:
    req = MagicMock()
    req.client.host = "203.0.113.7"
    req.headers = {}
    return req


async def _setup(db: AsyncSession, *, cv: str | None = "resumes/c/latest.pdf") -> dict[str, uuid.UUID]:
    ids = {k: uuid.uuid4() for k in ("co", "other", "hr", "applicant", "req", "enrol")}
    tag = ids["co"].hex[:10]
    db_rows = (
        ("INSERT INTO companies (id, name, slug) VALUES (:co, 'CV co', :s1), (:other, 'Other', :s2)",
         {"co": ids["co"], "other": ids["other"], "s1": f"cv-{tag}", "s2": f"cvo-{tag}"}),
        ("INSERT INTO users (id, email, company_id) VALUES (:hr, :m, :co)",
         {"hr": ids["hr"], "m": f"hr-{tag}@cv.test", "co": ids["co"]}),
        ("INSERT INTO applicants (id, company_id, full_name, target_job_title, resume_s3_key)"
         " VALUES (:a, :co, 'Asha', 'Engineer', :k)",
         {"a": ids["applicant"], "co": ids["co"], "k": cv}),
        ("INSERT INTO job_requisitions (id, company_id, title, created_at, updated_at)"
         " VALUES (:r, :co, 'Engineer', now(), now())",
         {"r": ids["req"], "co": ids["co"]}),
        ("INSERT INTO enrolments (id, company_id, requisition_id, applicant_id, target_job_title,"
         " applied_resume_s3_key, created_at, updated_at)"
         " VALUES (:e, :co, :r, :a, 'Engineer', 'resumes/c/applied-here.pdf', now(), now())",
         {"e": ids["enrol"], "co": ids["co"], "r": ids["req"], "a": ids["applicant"]}),
    )
    for sql, params in db_rows:
        await db.execute(text(sql), params)
    return ids


async def _view(db: AsyncSession, ids: dict[str, uuid.UUID], *, company: uuid.UUID | None = None,
                enrolment: uuid.UUID | None = None):  # noqa: ANN202
    return await hr_applicants.get_applicant_resume(
        ids["applicant"], _request(), (ids["hr"], company or ids["co"]), db,
        enrolment_id=enrolment,
    )


@pytest.mark.asyncio
async def test_hr_opens_the_latest_cv_as_an_uncached_inline_pdf(
    db: AsyncSession, storage: AsyncMock
) -> None:
    ids = await _setup(db)
    res = await _view(db, ids)
    assert res.body == PDF
    assert res.media_type == "application/pdf"
    assert res.headers["content-disposition"] == 'inline; filename="cv.pdf"'
    assert "no-store" in res.headers["cache-control"]
    assert res.headers["x-content-type-options"] == "nosniff"
    storage.assert_awaited_once_with("resumes/c/latest.pdf")


@pytest.mark.asyncio
async def test_with_an_application_it_opens_the_cv_that_application_was_sent_with(
    db: AsyncSession, storage: AsyncMock
) -> None:
    ids = await _setup(db)
    await _view(db, ids, enrolment=ids["enrol"])
    storage.assert_awaited_once_with("resumes/c/applied-here.pdf")


@pytest.mark.asyncio
async def test_every_view_is_audited_with_ids_only(db: AsyncSession, storage: AsyncMock) -> None:
    ids = await _setup(db)
    await _view(db, ids, enrolment=ids["enrol"])
    await db.flush()  # the route's commit is stubbed; a raw SQL read does not autoflush
    row = (await db.execute(
        text("SELECT actor_id, details FROM audit_log WHERE action = 'applicant.resume.viewed'"
             " AND resource_id = :a"), {"a": ids["applicant"]},
    )).one()
    assert row.actor_id == ids["hr"]
    assert row.details["enrolment_id"] == str(ids["enrol"])
    assert "Asha" not in str(row.details)


@pytest.mark.asyncio
async def test_another_company_gets_a_404(db: AsyncSession, storage: AsyncMock) -> None:
    ids = await _setup(db)
    with pytest.raises(HTTPException) as exc:
        await _view(db, ids, company=ids["other"])
    assert exc.value.status_code == 404
    storage.assert_not_awaited()


@pytest.mark.asyncio
async def test_an_application_of_someone_else_gets_a_404(db: AsyncSession, storage: AsyncMock) -> None:
    ids = await _setup(db)
    with pytest.raises(HTTPException) as exc:
        await _view(db, ids, enrolment=uuid.uuid4())
    assert exc.value.status_code == 404
    storage.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_cv_on_record_is_a_404(db: AsyncSession, storage: AsyncMock) -> None:
    ids = await _setup(db, cv=None)
    with pytest.raises(HTTPException) as exc:
        await _view(db, ids)
    assert exc.value.status_code == 404
    assert "No CV" in str(exc.value.detail)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["missing", "not_pdf"])
async def test_a_missing_or_non_pdf_object_is_never_served(
    db: AsyncSession, storage: AsyncMock, failure: str
) -> None:
    if failure == "missing":
        storage.side_effect = FileNotFoundError("gone")
    else:
        storage.return_value = b"<html><script>alert(1)</script>"
    ids = await _setup(db)
    with pytest.raises(HTTPException) as exc:
        await _view(db, ids)
    assert exc.value.status_code == 404
    # Nothing served, so nothing audited as viewed (flushed, so a pending row
    # would show).
    await db.flush()
    assert await db.scalar(
        text("SELECT count(*) FROM audit_log WHERE action = 'applicant.resume.viewed'"
             " AND resource_id = :a"), {"a": ids["applicant"]}) == 0



@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("applied", "scored", "expected"),
    [
        ("resumes/c/applied.pdf", "resumes/c/scored.pdf", "resumes/c/applied.pdf"),
        (None, "resumes/c/scored.pdf", "resumes/c/scored.pdf"),
        # An application from before CVs were pinned: the applicant's CV.
        (None, None, "resumes/c/latest.pdf"),
    ],
    ids=["applied-first", "then-scored", "then-the-applicant"],
)
async def test_which_cv_an_application_opens(
    db: AsyncSession, storage: AsyncMock, applied: str | None, scored: str | None, expected: str
) -> None:
    ids = await _setup(db)
    await db.execute(
        text("UPDATE enrolments SET applied_resume_s3_key = :a, scored_resume_s3_key = :s"
             " WHERE id = :e"),
        {"a": applied, "s": scored, "e": ids["enrol"]},
    )
    await _view(db, ids, enrolment=ids["enrol"])
    storage.assert_awaited_once_with(expected)


@pytest.mark.asyncio
async def test_a_storage_outage_is_not_reported_as_a_missing_cv(
    db: AsyncSession, storage: AsyncMock
) -> None:
    storage.side_effect = ConnectionError("storage unreachable")
    ids = await _setup(db)
    with pytest.raises(HTTPException) as exc:
        await _view(db, ids)
    assert exc.value.status_code == 503
    assert "Try again" in str(exc.value.detail)


@pytest.mark.asyncio
async def test_a_missing_s3_object_is_a_404(db: AsyncSession, storage: AsyncMock) -> None:
    class ClientError(Exception):
        response = {"Error": {"Code": "NoSuchKey"}}

    storage.side_effect = ClientError()
    ids = await _setup(db)
    with pytest.raises(HTTPException) as exc:
        await _view(db, ids)
    assert exc.value.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [b"%PDF-1.7\n1 0 obj << /JavaScript (app.alert(1)) >>",
     b"%PDF-1.7\n1 0 obj << /J#61vaScript (x) >>",  # #xx-escaped name
     b"%PDF-1.7\n1 0 obj << /Launch /F (cmd.exe) >>",
     b"%PDF-1.7\n1 0 obj << /EmbeddedFiles 2 0 R >>"],
    ids=["javascript", "escaped-javascript", "launch", "embedded-file"],
)
async def test_a_cv_with_active_content_is_refused_and_the_refusal_audited(
    db: AsyncSession, storage: AsyncMock, payload: bytes
) -> None:
    """Security review HIGH (ACCEPTED-RISKS AR-6): a CV can come from anyone
    through the public form; it is never served if it can run something."""
    storage.return_value = payload
    ids = await _setup(db)
    with pytest.raises(HTTPException) as exc:
        await _view(db, ids)
    assert exc.value.status_code == 422
    assert "scripts or embedded files" in str(exc.value.detail)
    await db.flush()
    actions = set((await db.execute(
        text("SELECT action FROM audit_log WHERE resource_id = :a"), {"a": ids["applicant"]},
    )).scalars().all())
    assert actions == {"applicant.resume.refused_active_content"}


@pytest.mark.asyncio
async def test_an_oversized_object_is_not_served(db: AsyncSession, storage: AsyncMock) -> None:
    storage.return_value = b"%PDF-" + b"0" * (2 * hr_applicants._MAX_RESUME_BYTES)
    ids = await _setup(db)
    with pytest.raises(HTTPException) as exc:
        await _view(db, ids)
    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_the_audit_says_which_file_was_served(db: AsyncSession, storage: AsyncMock) -> None:
    ids = await _setup(db)
    await _view(db, ids, enrolment=ids["enrol"])
    await db.flush()
    details = await db.scalar(
        text("SELECT details FROM audit_log WHERE action = 'applicant.resume.viewed'"
             " AND resource_id = :a"), {"a": ids["applicant"]})
    assert details["source"] == "applied"


@pytest.mark.parametrize(
    ("exc", "missing"),
    [
        (FileNotFoundError("x"), True),
        (type("ClientError", (Exception,), {"response": {"Error": {"Code": "NoSuchKey"}}})(), True),
        # An HTTP library's response OBJECT, not botocore's dict: an outage, and no crash.
        (type("HTTPError", (Exception,), {"response": object()})(), False),
        (ConnectionError("down"), False),
    ],
    ids=["file-not-found", "s3-no-such-key", "http-response-object", "connection"],
)
def test_what_counts_as_a_missing_cv(exc: Exception, missing: bool) -> None:
    assert hr_applicants._object_missing(exc) is missing


def test_misconfigured_local_storage_is_not_a_missing_cv() -> None:
    from app.local_storage import LocalStorageError

    assert hr_applicants._object_missing(LocalStorageError("no object at 'k'")) is True
    assert hr_applicants._object_missing(LocalStorageError("no object storage is configured")) is False
    assert hr_applicants._object_missing(LocalStorageError("unsafe object key: 'x'")) is False
