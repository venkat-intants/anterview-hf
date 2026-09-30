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

    # copy (5) -> score 50, violation_count STAYS 2 — restored (code review
    # FIX 1) with a severity weight, but never a violation (too many innocent
    # explanations for a bare clipboard event on its own).
    r = await client.post(
        "/exam/integrity-event", headers=headers,
        json={"attempt_id": attempt_id, "event_type": "copy"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["integrity_score"] == 50
    assert body["violation_count"] == 2

    # paste (10) -> score 40, violation_count STILL 2 — weighted higher than
    # copy (bringing content IN is judged stronger than copying OUT) but,
    # like copy, never a violation.
    r = await client.post(
        "/exam/integrity-event", headers=headers,
        json={"attempt_id": attempt_id, "event_type": "paste"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["integrity_score"] == 40
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
        json={"attempt_id": attempt_id, "event_type": "screenshot"},
    )
    assert r.status_code == 422, r.text

    # No metadata field exists at all (code review FIX 3) — refused as an
    # unrecognised field regardless of how innocuous its content looks, not
    # merely when it looks like a frame or a landmark array.
    r = await client.post(
        "/exam/integrity-event", headers=headers,
        json={
            "attempt_id": attempt_id, "event_type": "gaze_away",
            "started_at": "2026-09-29T00:03:00Z", "ended_at": "2026-09-29T00:03:02Z",
            "metadata": {"confidence": 0.9},
        },
    )
    assert r.status_code == 422, r.text

    event_count = await committed_db.scalar(
        text("SELECT count(*) FROM exam_integrity_events WHERE attempt_id = :i"),
        {"i": uuid.UUID(attempt_id)},
    )
    # Exactly the five ACCEPTED events above — the three refused payloads
    # (unknown type, missing ended_at, a metadata field) never reached the
    # table.
    assert int(event_count or 0) == 5

    # HR's proctoring read: score, per-type counts, durations, camera_in_use.
    r = await client.get(f"/hr/exams/{exam['exam_id']}/attempts/{attempt_id}/proctoring")
    assert r.status_code == 200, r.text
    report = r.json()
    assert report["camera_in_use"] is True
    assert report["integrity_score"] == 40
    assert report["counts"] == {
        "tab_blur": 1, "gaze_away": 1, "multiple_faces": 1, "copy": 1, "paste": 1,
    }
    by_type = {e["event_type"]: e for e in report["events"]}
    assert by_type["tab_blur"]["duration_seconds"] is None
    assert by_type["gaze_away"]["duration_seconds"] == 4.0
    assert by_type["multiple_faces"]["duration_seconds"] == 3.0
    assert by_type["copy"]["duration_seconds"] is None
    assert by_type["paste"]["duration_seconds"] is None

    # And the list view carries the same headline numbers, so HR does not
    # have to open every attempt to see whether one was watched at all.
    r = await client.get(f"/hr/exams/{exam['exam_id']}/attempts")
    assert r.status_code == 200, r.text
    row = next(a for a in r.json() if a["attempt_id"] == attempt_id)
    assert row["camera_in_use"] is True
    assert row["integrity_score"] == 40


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


# ---------------------------------------------------------------------------
# 5. Tenancy — one company can never read another's proctoring timeline
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_hr_proctoring_read_is_refused_across_companies(
    client: AsyncClient, committed_db: AsyncSession,
) -> None:
    """Code review gap: the proctoring read was correctly company-scoped but
    had no test proving it, and it returns candidate-derived behavioural data —
    the last endpoint that should be taken on trust.

    Asserts BOTH directions on the SAME url: 200 for the owning company, 404
    for the other. Without the 200 leg a broken url would make this test pass
    while proving nothing, which is the failure mode this repo keeps hitting.
    """
    tag = uuid.uuid4().hex[:10]
    owner_company, owner_hr = await _company(committed_db, f"own-{tag}")
    _hr(client, owner_hr, owner_company)
    exam = await _mixed_round_exam(client, camera_required=True)
    applicant_id = await _applicant(committed_db, owner_company, "Farida")
    token = await _assign(client, exam["exam_id"], exam["round_id"], applicant_id)
    headers = {"X-Exam-Token": token}

    await client.post("/exam/camera-consent", headers=headers)
    r = await client.post("/exam/start", headers=headers)
    assert r.status_code == 200, r.text
    attempt_id = r.json()["attempt_id"]
    r = await client.post(
        "/exam/integrity-event", headers=headers,
        json={"attempt_id": attempt_id, "event_type": "tab_blur"},
    )
    assert r.status_code == 200, r.text

    url = f"/hr/exams/{exam['exam_id']}/attempts/{attempt_id}/proctoring"

    # The owning company sees it.
    r = await client.get(url)
    assert r.status_code == 200, r.text
    assert r.json()["counts"] == {"tab_blur": 1}

    # A DIFFERENT company, same url, gets 404 — not 200, and not 403 either:
    # a 403 would confirm the attempt exists, which is itself a leak.
    other_company, other_hr = await _company(committed_db, f"oth-{tag}")
    _hr(client, other_hr, other_company)
    r = await client.get(url)
    assert r.status_code == 404, r.text

    # And the other company's attempts list does not carry the row at all.
    r = await client.get(f"/hr/exams/{exam['exam_id']}/attempts")
    assert r.status_code == 404, r.text

    # Security review LOW-3: everything above is satisfied by the EXAM
    # ownership gate alone (_get_owned_exam runs first), so deleting the
    # attempt query's own `company_id` filter would not have been caught.
    # Probe the cross-product explicitly: the other company's OWN exam, with
    # the first company's attempt id. The exam gate now passes — the caller
    # owns that exam — so only the attempt-level company filter can refuse it.
    other_exam = await _mixed_round_exam(client, camera_required=True)
    r = await client.get(
        f"/hr/exams/{other_exam['exam_id']}/attempts/{attempt_id}/proctoring"
    )
    assert r.status_code == 404, r.text

    # Sanity: that url shape IS reachable for an attempt the caller owns, so
    # the 404 above is about ownership and not about a malformed request.
    other_applicant = await _applicant(committed_db, other_company, "Gopal")
    other_token = await _assign(
        client, other_exam["exam_id"], other_exam["round_id"], other_applicant
    )
    other_headers = {"X-Exam-Token": other_token}
    await client.post("/exam/camera-consent", headers=other_headers)
    r = await client.post("/exam/start", headers=other_headers)
    assert r.status_code == 200, r.text
    r = await client.get(
        f"/hr/exams/{other_exam['exam_id']}/attempts/{r.json()['attempt_id']}/proctoring"
    )
    assert r.status_code == 200, r.text


# ---------------------------------------------------------------------------
# 6. Accepting the notice a SECOND time still leaves evidence
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_a_repeat_acceptance_is_evidenced_even_though_the_ledger_is_unchanged(
    client: AsyncClient, committed_db: AsyncSession,
) -> None:
    """Security review LOW-1, proven against a real database.

    dpdp_consent_ledger holds ONE ACTIVE row per (user, consent_type, purpose),
    so asking a candidate again — a second round, or someone who consented
    during the AI interview — writes no new ledger row. Before this change the
    only trace of the exam having asked at all was a log.info line.

    docs/DATA-FLOW.md tells bid readers "granting it once covers both doors", so
    this is a claim we can be asked to substantiate.

    The pair that matters: the ledger stays at ONE row (the partial unique index
    permits one active grant), and the audit trail also stays at ONE — because
    the acceptance is evidenced per ROUND, enforced by
    ix_audit_log_camera_notice_round and an ON CONFLICT DO NOTHING insert.
    audit_log is append-only and erasure-exempt, and this route is
    unauthenticated, so a row per POST would be permanent and unbounded.
    """
    tag = uuid.uuid4().hex[:10]
    company_id, hr_id = await _company(committed_db, tag)
    _hr(client, hr_id, company_id)
    exam = await _mixed_round_exam(client, camera_required=True)
    applicant_id = await _applicant(committed_db, company_id, "Hema")
    token = await _assign(client, exam["exam_id"], exam["round_id"], applicant_id)
    headers = {"X-Exam-Token": token}

    first = await client.post("/exam/camera-consent", headers=headers)
    assert first.status_code == 200, first.text
    assert first.json()["already_granted"] is False

    second = await client.post("/exam/camera-consent", headers=headers)
    assert second.status_code == 200, second.text
    assert second.json()["already_granted"] is True, "the grant should already exist"

    user_id = await committed_db.scalar(
        text("SELECT user_id FROM applicants WHERE id = :a"), {"a": applicant_id}
    )
    assert user_id is not None

    ledger_rows = await committed_db.scalar(
        text(
            "SELECT count(*) FROM dpdp_consent_ledger"
            " WHERE user_id = :u AND consent_type = 'video_capture'"
            "   AND granted AND revoked_at IS NULL"
        ),
        {"u": user_id},
    )
    assert ledger_rows == 1, "the partial unique index permits exactly one active grant"

    rows = (
        await committed_db.execute(
            text(
                "SELECT details, ip_address, user_agent FROM audit_log"
                " WHERE action = 'exam.camera_notice.accepted' AND actor_id = :u"
                " ORDER BY event_ts"
            ),
            {"u": user_id},
        )
    ).mappings().all()
    # ONE row, not two: the acceptance is evidenced per ROUND, not per POST.
    # audit_log is append-only (a trigger blocks DELETE) and excluded from
    # erasure, and this route is unauthenticated at 10/token/minute, so a row
    # per request would be permanent and unbounded (code review SHOULD FIX 1).
    assert len(rows) == 1, (
        "a repeat acceptance for the same round must not add a second permanent row"
    )
    d = rows[0]["details"]
    # The evidence the ledger cannot give: what wording, for which round, when.
    assert d["notice_version"]
    assert d["accepted_at_iso"]
    assert d["applicant_id"] == str(applicant_id)
    assert d["exam_round_id"] == str(exam["round_id"])
    assert d["already_granted"] is False, "the first acceptance minted the grant"
    assert d["consent_id"], "the grant is part of the uniqueness key"
    # NO request metadata at all: an ip_hash here would be pseudonymous personal
    # data kept forever in a table erasure cannot reach, for no purpose this row
    # serves (security review L1, DPDP s6(1)).
    assert rows[0]["ip_address"] is None
    assert rows[0]["user_agent"] is None
    for banned in ("ip_hash", "ua_hash"):
        assert banned not in d

    # The bound is the DATABASE's, not the application's. A third acceptance
    # conflicts on ix_audit_log_camera_notice_round and is silently dropped —
    # this is what a SELECT-then-INSERT probe could not guarantee under
    # concurrency (security review M1/M2).
    third = await client.post("/exam/camera-consent", headers=headers)
    assert third.status_code == 200, third.text
    still_one = await committed_db.scalar(
        text(
            "SELECT count(*) FROM audit_log"
            " WHERE action = 'exam.camera_notice.accepted' AND actor_id = :u"
        ),
        {"u": user_id},
    )
    assert still_one == 1, "the unique index must hold the trail at one row per round"
