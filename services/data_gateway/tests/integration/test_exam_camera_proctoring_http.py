"""Camera proctoring for exams — end to end through the real ASGI app.

DB-free coverage (the weighted score, the event vocabulary, the "no frame
ever" structural guarantee at the schema level, and the hiring-decision
guardrail) lives in ``tests/unit/test_exam_camera_proctoring.py``. This file
is everything that needs the real HTTP surface and a real Postgres: the
``video_capture`` consent gate on an unauthenticated magic link, the round's
own camera-required setting, the weighted rolling score across mixed browser
and camera events, the refusal of a camera event on a non-camera attempt, the
accommodation relaxation reaching the camera signals, and the HR proctoring
read (including "camera not in use" rather than zeros).

On the ``test_ph5_e3_pools_http.py`` / ``smoke_ph4_d2_accommodations.py``
precedent: a real ASGI app (with lifespan) over ``httpx.ASGITransport``,
against this repo's disposable Postgres, with ``get_hr_company`` overridden
(the candidate ``/exam/*`` routes need no auth override — the magic-link
token IS their auth). Seeds its own company per test (unique slug); leaves
rows behind, like the other exam-flow integration tests here.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from shared.db.engine import build_engine, build_session_factory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def committed_db() -> AsyncIterator[AsyncSession]:
    """A session that COMMITS, on its own engine — the ASGI app reads through
    its own connection and cannot see an uncommitted write on this one."""
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
        app.dependency_overrides.clear()


async def _company(db: AsyncSession, tag: str) -> tuple[uuid.UUID, uuid.UUID]:
    """One company + one hr_manager user (not role-checked here — HrCtxDep is
    overridden directly, the smoke-script precedent)."""
    company_id, hr_id = uuid.uuid4(), uuid.uuid4()
    await db.execute(
        text("INSERT INTO companies (id, name, slug) VALUES (:c, 'Camera co', :s)"),
        {"c": company_id, "s": f"camera-{tag}"},
    )
    await db.execute(
        text("INSERT INTO users (id, email, company_id) VALUES (:u, :e, :c)"),
        {"u": hr_id, "e": f"hr-{tag}@camera.test", "c": company_id},
    )
    await db.commit()
    return company_id, hr_id


def _hr(client: AsyncClient, hr_id: uuid.UUID, company_id: uuid.UUID) -> None:
    from app.dependencies import get_hr_company
    from app.main import app

    app.dependency_overrides[get_hr_company] = lambda: (hr_id, company_id)


async def _mixed_round_exam(client: AsyncClient, *, camera_required: bool) -> dict[str, object]:
    """A published, MIXED (mcq + coding) round, with the requested camera
    setting — exercising 'coding is a section kind, not a separate flow' for
    free: proctoring below never branches on which section the round has."""
    r = await client.post("/hr/exams", json={"title": "Camera exam", "kind": "mcq"})
    assert r.status_code == 201, r.text
    exam_id = r.json()["id"]

    r = await client.get(f"/hr/exams/{exam_id}/structure")
    round_id = r.json()["rounds"][0]["id"]
    mcq_section_id = r.json()["rounds"][0]["sections"][0]["id"]

    r = await client.patch(
        f"/hr/exams/{exam_id}/rounds/{round_id}",
        json={"camera_proctoring_required": camera_required, "pass_threshold": 50},
    )
    assert r.status_code == 200, r.text
    assert r.json()["camera_proctoring_required"] is camera_required

    r = await client.post(
        f"/hr/exams/{exam_id}/sections/{mcq_section_id}/questions",
        json={"prompt": "2+2?", "options": ["3", "4"], "correct_index": 1, "points": 1},
    )
    assert r.status_code == 201, r.text

    r = await client.post(
        f"/hr/exams/{exam_id}/rounds/{round_id}/sections",
        json={"title": "Coding", "kind": "coding"},
    )
    assert r.status_code == 201, r.text
    coding_section_id = r.json()["id"]
    r = await client.post(
        f"/hr/exams/{exam_id}/sections/{coding_section_id}/coding-questions",
        json={
            "prompt": "Add two numbers", "allowed_languages": ["python"],
            "test_cases": [{"stdin": "1 2", "expected_output": "3", "is_sample": True, "weight": 1}],
            "time_limit_ms": 2000, "points": 100,
        },
    )
    assert r.status_code == 201, r.text

    r = await client.patch(f"/hr/exams/{exam_id}/rounds/{round_id}", json={"status": "published"})
    assert r.status_code == 200 and r.json()["status"] == "published", r.text

    return {"exam_id": exam_id, "round_id": round_id}


async def _applicant(db: AsyncSession, company_id: uuid.UUID, name: str) -> uuid.UUID:
    applicant_id = uuid.uuid4()
    await db.execute(
        text(
            "INSERT INTO applicants (id, company_id, full_name, target_job_title)"
            " VALUES (:i, :c, :n, 'Engineer')"
        ),
        {"i": applicant_id, "c": company_id, "n": name},
    )
    await db.commit()
    return applicant_id


async def _assign(
    client: AsyncClient, exam_id: str, round_id: str, applicant_id: uuid.UUID,
    *, enrolment_ids: list[uuid.UUID] | None = None,
) -> str:
    body: dict[str, object] = {"round_id": round_id}
    if enrolment_ids:
        body["enrolment_ids"] = [str(e) for e in enrolment_ids]
    else:
        body["applicant_ids"] = [str(applicant_id)]
    r = await client.post(f"/hr/exams/{exam_id}/assignments", json=body)
    assert r.status_code == 201, r.text
    return r.json()[0]["magic_link"].split("#")[-1]


# ---------------------------------------------------------------------------
# 1. The consent gate, camera-required round
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_camera_required_round_refuses_start_without_consent(
    client: AsyncClient, committed_db: AsyncSession,
) -> None:
    tag = uuid.uuid4().hex[:10]
    company_id, hr_id = await _company(committed_db, tag)
    _hr(client, hr_id, company_id)
    exam = await _mixed_round_exam(client, camera_required=True)
    applicant_id = await _applicant(committed_db, company_id, "Asha")
    token = await _assign(client, exam["exam_id"], exam["round_id"], applicant_id)
    headers = {"X-Exam-Token": token}

    r = await client.get("/exam", headers=headers)
    assert r.status_code == 200, r.text
    take = r.json()
    assert take["camera_required"] is True
    assert take["camera_consent_granted"] is False

    r = await client.post("/exam/start", headers=headers)
    assert r.status_code == 422, r.text

    r = await client.post("/exam/camera-consent", headers=headers)
    assert r.status_code == 200, r.text
    consent = r.json()
    assert consent["consented"] is True
    assert consent["already_granted"] is False

    # Idempotent: a repeat grant does not mint a second ledger row.
    r = await client.post("/exam/camera-consent", headers=headers)
    assert r.json()["already_granted"] is True

    r = await client.get("/exam", headers=headers)
    assert r.json()["camera_consent_granted"] is True

    r = await client.post("/exam/start", headers=headers)
    assert r.status_code == 200, r.text
    start = r.json()
    assert start["camera_in_use"] is True

    count = await committed_db.scalar(
        text(
            "SELECT count(*) FROM dpdp_consent_ledger"
            " WHERE consent_type = 'video_capture' AND purpose = 'interview'"
            "   AND granted AND revoked_at IS NULL"
        )
    )
    assert int(count or 0) >= 1


# ---------------------------------------------------------------------------
# 2. Camera NOT required: no gate, and the attempt records it was never used
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_camera_not_required_round_never_gates_and_records_not_in_use(
    client: AsyncClient, committed_db: AsyncSession,
) -> None:
    tag = uuid.uuid4().hex[:10]
    company_id, hr_id = await _company(committed_db, tag)
    _hr(client, hr_id, company_id)
    exam = await _mixed_round_exam(client, camera_required=False)
    applicant_id = await _applicant(committed_db, company_id, "Bilal")
    token = await _assign(client, exam["exam_id"], exam["round_id"], applicant_id)
    headers = {"X-Exam-Token": token}

    r = await client.get("/exam", headers=headers)
    assert r.json()["camera_required"] is False

    r = await client.post("/exam/start", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["camera_in_use"] is False
    attempt_id = r.json()["attempt_id"]

    # A camera event on an attempt that was never watched is refused outright
    # — it can only be forged or stale, never a real signal.
    r = await client.post(
        "/exam/integrity-event", headers=headers,
        json={
            "attempt_id": attempt_id, "event_type": "gaze_away",
            "started_at": "2026-09-29T00:00:00Z", "ended_at": "2026-09-29T00:00:05Z",
        },
    )
    assert r.status_code == 422, r.text

    row = (
        await committed_db.execute(
            text("SELECT camera_in_use, integrity_score FROM exam_attempts WHERE id = :i"),
            {"i": uuid.UUID(attempt_id)},
        )
    ).first()
    assert row is not None
    assert row[0] is False
    # No event was ever accepted, so the score is still the clean default.
    assert row[1] is None or row[1] == 100


# ---------------------------------------------------------------------------
# 3. The weighted score, mixed browser + camera events; HR's proctoring read
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_weighted_score_and_hr_proctoring_read(
    client: AsyncClient, committed_db: AsyncSession,
) -> None:
    tag = uuid.uuid4().hex[:10]
    company_id, hr_id = await _company(committed_db, tag)
    _hr(client, hr_id, company_id)
    exam = await _mixed_round_exam(client, camera_required=True)
    applicant_id = await _applicant(committed_db, company_id, "Chetan")
    token = await _assign(client, exam["exam_id"], exam["round_id"], applicant_id)
    headers = {"X-Exam-Token": token}

    await client.post("/exam/camera-consent", headers=headers)
    r = await client.post("/exam/start", headers=headers)
    attempt_id = r.json()["attempt_id"]

    # tab_blur (15) -> score 85, violation_count 1
    r = await client.post(
        "/exam/integrity-event", headers=headers,
        json={"attempt_id": attempt_id, "event_type": "tab_blur"},
    )
    assert r.status_code == 200, r.text
    assert r.json() == {
        "accepted": True, "violation_count": 1, "max_violations": 3, "integrity_score": 85,
    }

    # gaze_away (5) -> score 80, violation_count STAYS 1 (never a violation)
    r = await client.post(
        "/exam/integrity-event", headers=headers,
        json={
            "attempt_id": attempt_id, "event_type": "gaze_away",
            "started_at": "2026-09-29T00:00:00Z", "ended_at": "2026-09-29T00:00:04Z",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["integrity_score"] == 80
    assert body["violation_count"] == 1

    # multiple_faces (25) -> score 55, violation_count 2
    r = await client.post(
        "/exam/integrity-event", headers=headers,
        json={
            "attempt_id": attempt_id, "event_type": "multiple_faces",
            "started_at": "2026-09-29T00:01:00Z", "ended_at": "2026-09-29T00:01:03Z",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["integrity_score"] == 55
    assert body["violation_count"] == 2

    # A ranged event with no ended_at is refused, not stored.
    r = await client.post(
        "/exam/integrity-event", headers=headers,
        json={
            "attempt_id": attempt_id, "event_type": "face_absent",
            "started_at": "2026-09-29T00:02:00Z",
        },
    )
    assert r.status_code == 422, r.text

    # An unknown event type is refused, not stored.
    r = await client.post(
        "/exam/integrity-event", headers=headers,
        json={"attempt_id": attempt_id, "event_type": "copy"},
    )
    assert r.status_code == 422, r.text

    # No frame, image or landmark data — ever. Refused, and no row created.
    r = await client.post(
        "/exam/integrity-event", headers=headers,
        json={
            "attempt_id": attempt_id, "event_type": "gaze_away",
            "started_at": "2026-09-29T00:03:00Z", "ended_at": "2026-09-29T00:03:02Z",
            "metadata": {"landmarks": [[0.1, 0.2, 0.0]] * 468},
        },
    )
    assert r.status_code == 422, r.text

    event_count = await committed_db.scalar(
        text("SELECT count(*) FROM exam_integrity_events WHERE attempt_id = :i"),
        {"i": uuid.UUID(attempt_id)},
    )
    # Exactly the three ACCEPTED events above — the four refused payloads
    # (unknown type, missing ended_at, landmark array, camera-off case tested
    # elsewhere) never reached the table.
    assert int(event_count or 0) == 3

    # HR's proctoring read: score, per-type counts, durations, camera_in_use.
    r = await client.get(f"/hr/exams/{exam['exam_id']}/attempts/{attempt_id}/proctoring")
    assert r.status_code == 200, r.text
    report = r.json()
    assert report["camera_in_use"] is True
    assert report["integrity_score"] == 55
    assert report["counts"] == {"tab_blur": 1, "gaze_away": 1, "multiple_faces": 1}
    by_type = {e["event_type"]: e for e in report["events"]}
    assert by_type["tab_blur"]["duration_seconds"] is None
    assert by_type["gaze_away"]["duration_seconds"] == 4.0
    assert by_type["multiple_faces"]["duration_seconds"] == 3.0

    # And the list view carries the same headline numbers, so HR does not
    # have to open every attempt to see whether one was watched at all.
    r = await client.get(f"/hr/exams/{exam['exam_id']}/attempts")
    assert r.status_code == 200, r.text
    row = next(a for a in r.json() if a["attempt_id"] == attempt_id)
    assert row["camera_in_use"] is True
    assert row["integrity_score"] == 55


# ---------------------------------------------------------------------------
# 4. Accommodation relaxation reaches the camera signals too
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_relaxed_accommodation_suppresses_auto_submit_for_camera_events_too(
    client: AsyncClient, committed_db: AsyncSession,
) -> None:
    tag = uuid.uuid4().hex[:10]
    company_id, hr_id = await _company(committed_db, tag)
    _hr(client, hr_id, company_id)
    exam = await _mixed_round_exam(client, camera_required=True)
    applicant_id = await _applicant(committed_db, company_id, "Deepa")

    # An application to scope the accommodation to (exam_round_id needs one).
    req_id, enrolment_id = uuid.uuid4(), uuid.uuid4()
    now = datetime.now(tz=UTC)
    await committed_db.execute(
        text(
            "INSERT INTO job_requisitions (id, company_id, title, created_at, updated_at)"
            " VALUES (:i, :c, 'Engineer', :n, :n)"
        ),
        {"i": req_id, "c": company_id, "n": now},
    )
    await committed_db.execute(
        text(
            "INSERT INTO enrolments (id, company_id, requisition_id, applicant_id,"
            " target_job_title, created_at, updated_at)"
            " VALUES (:e, :c, :r, :a, 'Engineer', :n, :n)"
        ),
        {"e": enrolment_id, "c": company_id, "r": req_id, "a": applicant_id, "n": now},
    )
    await committed_db.commit()

    r = await client.post(
        f"/hr/applicants/{applicant_id}/accommodations",
        json={
            "enrolment_id": str(enrolment_id), "exam_round_id": exam["round_id"],
            "relax_auto_submit": True, "basis": "hr_initiated",
        },
    )
    assert r.status_code == 201, r.text

    token = await _assign(
        client, exam["exam_id"], exam["round_id"], applicant_id, enrolment_ids=[enrolment_id],
    )
    headers = {"X-Exam-Token": token}
    await client.post("/exam/camera-consent", headers=headers)
    r = await client.post("/exam/start", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["max_violations"] is None  # relaxed from the very first response
    attempt_id = r.json()["attempt_id"]

    # face_absent is one of the two camera events that WOULD count as a
    # violation for an unrelaxed candidate — max_violations must stay None.
    r = await client.post(
        "/exam/integrity-event", headers=headers,
        json={
            "attempt_id": attempt_id, "event_type": "face_absent",
            "started_at": "2026-09-29T00:00:00Z", "ended_at": "2026-09-29T00:00:10Z",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["max_violations"] is None
    # The event is still recorded and still scored — relaxation is about
    # auto-submit, never about hiding the signal from HR.
    assert body["violation_count"] == 1
    assert body["integrity_score"] == 100 - 20
