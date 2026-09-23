"""PH5 Wave 1 — C1-2 ("sources are retained throughout the lifecycle"),
against real Postgres.

``tests/unit/test_ph3_source_tracking.py::test_only_enrol_applicant_writes_
enrolments_source`` proves nothing OUTSIDE ``app.workflow_runner.enrol_
applicant`` can even WRITE the column. This file proves the one real
hand-off that never went through a dedicated test before: HR names a
channel on a BULK UPLOAD (a value that lives on ``upload_batches``, not
``enrolments``, until the reconciler's background pass turns a stored file
into an application), and that value must still be the one on the
resulting application's ``enrolments.source`` after the application moves
through the stage ledger — because nothing else ever touches that column
again.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from shared.auth.jwt import issue_access_token
from shared.db.engine import build_engine, build_session_factory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def committed_db() -> AsyncIterator[AsyncSession]:
    """A session that COMMITS — the app under test reads through its OWN
    connection (a real ASGI client), which cannot see an uncommitted write on
    this one. This is our own throwaway database, so nothing here needs
    cleaning up afterwards: every test uses fresh random ids."""
    engine = build_engine(
        database_url=settings.database_url, database_ssl=settings.database_ssl, pool_size=2,
    )
    factory = build_session_factory(engine)
    try:
        async with factory() as session:
            yield session
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    from app.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", timeout=30.0,
    ) as ac, app.router.lifespan_context(app):
        yield ac


def _token(user_id: uuid.UUID, roles: list[str]) -> str:
    return issue_access_token(
        str(user_id), roles, settings.jwt_secret, issuer=settings.jwt_issuer,
        audience=settings.jwt_audience,
    )


def _auth(user_id: uuid.UUID, roles: list[str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {_token(user_id, roles)}"}


def _tiny_pdf(line: str) -> bytes:
    """A minimal one-page PDF ``pypdf`` can extract text from — same
    construction as ``tests/integration/smoke_group_e_bulk.py``."""
    stream = f"BT /F1 12 Tf 72 720 Td ({line}) Tj ET".encode()
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R"
        b" /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += str(i).encode() + b" 0 obj\n" + o + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 " + str(len(objs) + 1).encode() + b"\n0000000000 65535 f \n"
    for off in offsets:
        out += ("%010d 00000 n \n" % off).encode()
    out += (b"trailer\n<< /Size " + str(len(objs) + 1).encode() + b" /Root 1 0 R >>\n"
            b"startxref\n" + str(xref).encode() + b"\n%%EOF")
    return bytes(out)


async def _seed(db: AsyncSession) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """(company_id, hr_user_id, requisition_id)."""
    company, hr, requisition = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    now = datetime.now(tz=UTC)
    await db.execute(
        text("INSERT INTO companies (id, name, slug) VALUES (:c, :n, :s)"),
        {"c": company, "n": f"Src Co {company.hex[:6]}", "s": f"src-{company.hex[:8]}"},
    )
    await db.execute(
        text("INSERT INTO users (id, email, company_id) VALUES (:u, :e, :c)"),
        {"u": hr, "e": f"hr-{hr.hex[:8]}@src.test", "c": company},
    )
    await db.execute(
        text(
            "INSERT INTO job_requisitions (id, company_id, title, status, created_at, updated_at)"
            " VALUES (:r, :c, 'Engineer', 'open', :n, :n)"
        ),
        {"r": requisition, "c": company, "n": now},
    )
    await db.commit()
    return company, hr, requisition


async def test_a_bulk_uploads_source_survives_the_hand_off_and_later_stage_moves(
    committed_db: AsyncSession, client: AsyncClient, monkeypatch: pytest.MonkeyPatch,
    tmp_path: object,
) -> None:
    """HR uploads one CV to a batch tagged ``source=referral``. The
    reconciler's background pass (never the request) turns it into an
    application; that application's ``enrolments.source`` must read
    ``referral`` — not ``internal`` (the batch's own default) and not
    ``unknown`` — and must STILL read ``referral`` after the application is
    screened and hired, since nothing but ``enrol_applicant`` ever writes the
    column (see the unit-level scan test)."""
    import app.reconciliation as rec  # noqa: PLC0415 — ingest_pass imports this lazily too
    from app.bulk_ingest import ingest_pass

    # Local-disk storage instead of real S3/R2 — the smoke test's own pattern.
    monkeypatch.setattr(settings, "storage_local_dir", str(tmp_path))
    monkeypatch.setattr(settings, "s3_access_key_id", "")
    monkeypatch.setattr(settings, "app_env", "development")

    company, hr, requisition = await _seed(committed_db)
    headers = _auth(hr, ["hr_manager"])

    files = [("files", ("Anita_Rao.pdf", _tiny_pdf("Anita Rao referred by a friend"), "application/pdf"))]
    resp = await client.post(
        "/hr/applicants/bulk", headers=headers, files=files,
        data={"requisition_id": str(requisition), "source": "referral"},
    )
    assert resp.status_code == 202, resp.text
    batch_id = resp.json()["batch_id"]

    # The channel is recorded on the BATCH straight away, at request time.
    batch_source = await committed_db.scalar(
        text("SELECT source FROM upload_batches WHERE id = :b"), {"b": batch_id},
    )
    assert batch_source == "referral"

    # The reconciler's background pass — not the request — creates the
    # applicant and the enrolment.
    result = rec.PassResult()
    await ingest_pass(committed_db, result)
    assert result.ingested == 1, result.as_dict()

    row = (
        await committed_db.execute(
            text(
                "SELECT e.id, e.source FROM enrolments e"
                " JOIN upload_items i ON i.enrolment_id = e.id"
                " WHERE i.batch_id = :b"
            ),
            {"b": batch_id},
        )
    ).mappings().one()
    assert row["source"] == "referral", "the application must carry the BATCH's channel"
    enrolment_id = row["id"]

    # Move it through the ledger: screened, then hired. Nothing here ever
    # touches `source` — this is the assertion the whole test exists for.
    await committed_db.execute(
        text(
            "INSERT INTO stage_transitions (company_id, enrolment_id, to_status, automated,"
            " occurred_at) VALUES (:c, :e, 'shortlisted', false, now())"
        ),
        {"c": company, "e": enrolment_id},
    )
    await committed_db.execute(
        text("UPDATE enrolments SET status = 'shortlisted', updated_at = now() WHERE id = :e"),
        {"e": enrolment_id},
    )
    await committed_db.execute(
        text(
            "INSERT INTO stage_transitions (company_id, enrolment_id, to_status, automated,"
            " occurred_at, reason_code, reason_label)"
            " VALUES (:c, :e, 'hired', false, now(), 'test_reason', 'Test reason')"
        ),
        {"c": company, "e": enrolment_id},
    )
    await committed_db.execute(
        text("UPDATE enrolments SET status = 'hired', updated_at = now() WHERE id = :e"),
        {"e": enrolment_id},
    )
    await committed_db.commit()

    after_stage_moves = await committed_db.scalar(
        text("SELECT source FROM enrolments WHERE id = :e"), {"e": enrolment_id},
    )
    assert after_stage_moves == "referral", "a stage move must never rewrite the channel"


async def test_a_bulk_upload_with_no_channel_falls_back_to_internal(
    committed_db: AsyncSession, client: AsyncClient, monkeypatch: pytest.MonkeyPatch,
    tmp_path: object,
) -> None:
    """The companion case: HR skips the (optional) channel field, and the
    application still lands as `internal` — never `unknown`, which would
    misreport an HR-collected upload as untracked."""
    import app.reconciliation as rec  # noqa: PLC0415
    from app.bulk_ingest import ingest_pass

    monkeypatch.setattr(settings, "storage_local_dir", str(tmp_path))
    monkeypatch.setattr(settings, "s3_access_key_id", "")
    monkeypatch.setattr(settings, "app_env", "development")

    _company, hr, requisition = await _seed(committed_db)
    headers = _auth(hr, ["hr_manager"])

    files = [("files", ("Bala_K.pdf", _tiny_pdf("Bala K no channel stated"), "application/pdf"))]
    resp = await client.post(
        "/hr/applicants/bulk", headers=headers, files=files,
        data={"requisition_id": str(requisition)},
    )
    assert resp.status_code == 202, resp.text
    batch_id = resp.json()["batch_id"]

    result = rec.PassResult()
    await ingest_pass(committed_db, result)
    assert result.ingested == 1, result.as_dict()

    source = await committed_db.scalar(
        text(
            "SELECT e.source FROM enrolments e JOIN upload_items i ON i.enrolment_id = e.id"
            " WHERE i.batch_id = :b"
        ),
        {"b": batch_id},
    )
    assert source == "internal"
