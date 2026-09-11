"""Unit tests for the Group A reliability foundation (A1–A6).

DB is mocked throughout, matching the house pattern for this suite. What is
tested here is the logic that decides *whether* to act — backoff, give-up,
claim, dedupe-key shape, localisation — because that is where the bugs that
matter live. The SQL itself is exercised by live verification.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest


class _FakeFactory:
    """Stands in for an ``async_sessionmaker`` — callable, async context manager."""

    def __init__(self, db: AsyncMock) -> None:
        self._db = db

    def __call__(self) -> _FakeFactory:
        return self

    async def __aenter__(self) -> AsyncMock:
        return self._db

    async def __aexit__(self, *_: object) -> bool:
        return False


def _db(*, scalar: object = None) -> AsyncMock:
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=scalar)
    db.execute = AsyncMock()
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    return db


# ===========================================================================
# A3 — lifecycle email templates
# ===========================================================================
_NEW_TEMPLATES = ("exam_reminder", "interview_reminder", "link_expired", "results_ready")

_CTX = {
    "exam_reminder": {"name": "Priya", "exam_title": "Aptitude", "expires": "03 Sep, 6 PM IST"},
    "interview_reminder": {"name": "Ravi", "job_title": "Python Developer", "when": "02 Sep"},
    "link_expired": {"name": "Anita", "what": "Technical Round", "kind": "exam"},
    "results_ready": {"name": "Kiran", "job_title": "Staff Nurse", "cta_url": "https://x/history"},
}


@pytest.mark.parametrize("template", _NEW_TEMPLATES)
@pytest.mark.parametrize("lang", ["en", "hi", "te"])
def test_template_renders_in_every_day_one_language(template: str, lang: str) -> None:
    """Hard constraint #5: candidate-facing copy must exist in EN/HI/TE."""
    from app.email_templates import render

    r = render(template, lang, _CTX[template])
    assert r.subject.strip(), "empty subject"
    assert "<table" in r.html.lower(), "not wrapped in the branded layout"
    assert r.text.strip(), "empty plain-text part"


@pytest.mark.parametrize("template", _NEW_TEMPLATES)
def test_template_is_actually_localised(template: str) -> None:
    """A template registered but not translated would silently ship English."""
    from app.email_templates import render

    en = render(template, "en", _CTX[template]).subject
    assert render(template, "hi", _CTX[template]).subject != en
    assert render(template, "te", _CTX[template]).subject != en


@pytest.mark.parametrize("template", _NEW_TEMPLATES)
def test_template_survives_empty_context(template: str) -> None:
    """These render inside a background sweep — a missing key must not raise."""
    from app.email_templates import render

    assert render(template, "en", {}).subject.strip()


def test_template_escapes_candidate_supplied_name() -> None:
    """full_name is candidate-supplied and reaches the HTML body."""
    from app.email_templates import render

    r = render("results_ready", "en", {**_CTX["results_ready"], "name": "<script>alert(1)</script>"})
    assert "<script>alert" not in r.html
    assert "&lt;script&gt;" in r.html


def test_exam_reminder_distinguishes_the_two_windows() -> None:
    """A 1h nudge that reads like the 24h one trains people to ignore both."""
    from app.email_templates import render

    ctx = _CTX["exam_reminder"]
    day = render("exam_reminder", "en", {**ctx, "window": "24h"})
    hour = render("exam_reminder", "en", {**ctx, "window": "1h"})
    # Assert the property, not a particular wording: the subject must differ and
    # the body must state the tighter deadline. Pinning the copy itself would
    # make every future edit to the wording a test failure.
    assert day.subject != hour.subject
    assert "tomorrow" in day.text.lower()
    assert "within the hour" in hour.text.lower()


def test_link_expired_never_implies_rejection() -> None:
    """D-05: missing a window is not a rejection, and this email must not say so."""
    from app.email_templates import render

    for lang in ("en", "hi", "te"):
        body = render("link_expired", lang, _CTX["link_expired"]).text.lower()
        for forbidden in ("reject", "unsuccessful", "no longer being considered"):
            assert forbidden not in body


def test_results_ready_carries_no_score() -> None:
    """A bare composite in an inbox is the worst framing of an assessment."""
    from app.email_templates import render

    r = render("results_ready", "en", {**_CTX["results_ready"], "score": 7.4, "composite": 7.4})
    assert "7.4" not in r.html
    assert "7.4" not in r.text


def test_results_ready_falls_back_when_no_link() -> None:
    from app.email_templates import render

    r = render("results_ready", "en", {**_CTX["results_ready"], "cta_url": None})
    assert "sign in" in r.text.lower()


# ===========================================================================
# A1 — reconciliation
# ===========================================================================
def test_backoff_grows_and_is_capped() -> None:
    from app.reconciliation import _BACKOFF_CAP_SECONDS, _backoff

    seq = [_backoff(n).total_seconds() for n in range(1, 12)]
    assert seq == sorted(seq), "backoff must never shrink"
    assert seq[0] == 300
    assert max(seq) <= _BACKOFF_CAP_SECONDS


@pytest.mark.asyncio
async def test_record_failure_schedules_a_retry_before_the_limit() -> None:
    from app.reconciliation import KIND_ATS, _record_failure

    db = _db()
    db.execute.return_value = MagicMock(first=MagicMock(return_value=(3,)))
    gave_up = await _record_failure(db, KIND_ATS, uuid.uuid4(), "boom")
    assert gave_up is False
    sql = " ".join(str(c.args[0]) for c in db.execute.call_args_list)
    assert "next_attempt_at" in sql
    assert "gave_up_at" not in sql


@pytest.mark.asyncio
async def test_record_failure_parks_the_row_at_the_limit() -> None:
    """A row that cannot be fixed must stop consuming the batch every cycle."""
    from app.reconciliation import KIND_ATS, MAX_ATTEMPTS, _record_failure

    db = _db()
    db.execute.return_value = MagicMock(first=MagicMock(return_value=(MAX_ATTEMPTS,)))
    gave_up = await _record_failure(db, KIND_ATS, uuid.uuid4(), "boom")
    assert gave_up is True
    sql = " ".join(str(c.args[0]) for c in db.execute.call_args_list)
    assert "gave_up_at" in sql


@pytest.mark.asyncio
async def test_one_bad_applicant_does_not_stop_the_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The whole point of the loop is that it keeps going."""
    import app.reconciliation as rec

    good, bad = uuid.uuid4(), uuid.uuid4()
    db = _db()
    db.execute.return_value = MagicMock(all=MagicMock(return_value=[(bad,), (good,)]))

    applicant = MagicMock(
        id=good, company_id=uuid.uuid4(), resume_text="cv", target_job_title="Dev",
        target_level="mid", target_jd_text=None, created_by_user_id=uuid.uuid4(),
        upload_batch_id=None,
    )
    db.get = AsyncMock(return_value=applicant)

    calls: list[uuid.UUID] = []

    async def _score(**_: object) -> dict[str, int]:
        calls.append(bad if len(calls) == 0 else good)
        if len(calls) == 1:
            raise RuntimeError("scorer down")
        return {"overall": 7}

    # Through monkeypatch, not a raw assignment: a raw one is never put back
    # and leaks the stub into every later test that touches the scorer.
    monkeypatch.setattr(rec, "score_resume_remote", _score)
    result = rec.PassResult()
    await rec._score_pass(db, result)

    assert result.failed == 1, "the failing row was not recorded"
    assert result.scored == 1, "the pass stopped instead of continuing"


# ===========================================================================
# A1 — interviews without a scorecard, scorecards without a PDF
# ===========================================================================
def test_min_answers_matches_the_worker() -> None:
    """The retry must use the worker's own "long enough to score" rule. The two
    services share no code, so the value is mirrored — and pinned here."""
    import re
    from pathlib import Path

    from app.reconciliation import MIN_ANSWERS_TO_SCORE

    worker = (
        Path(__file__).resolve().parents[4]
        / "services" / "interview_core" / "app" / "worker" / "constants.py"
    )
    if not worker.exists():
        pytest.skip("interview_core source not present in this checkout")
    m = re.search(r"^MIN_ANSWERS_TO_SCORE:\s*int\s*=\s*(\d+)", worker.read_text(), re.M)
    assert m, "MIN_ANSWERS_TO_SCORE not found in interview_core constants"
    assert int(m.group(1)) == MIN_ANSWERS_TO_SCORE


def test_only_interviews_the_worker_would_have_scored_are_retried() -> None:
    """'abandoned' covers a short interview and a crash the reaper finalised.
    Neither is scored automatically: a scorecard from a truncated transcript
    reads as a complete assessment."""
    from app.reconciliation import _UNSCORED_SQL

    assert "s.status = 'completed'" in _UNSCORED_SQL
    assert "abandoned" not in _UNSCORED_SQL
    assert "NOT EXISTS (SELECT 1 FROM scorecards sc WHERE sc.session_id = s.id)" in _UNSCORED_SQL
    assert ">= :min_answers" in _UNSCORED_SQL


def test_retry_respects_consent_and_pending_erasure() -> None:
    from app.reconciliation import _UNSCORED_SQL

    assert "c.consent_type = 'interview_voice_recording'" in _UNSCORED_SQL
    assert "c.revoked_at IS NULL" in _UNSCORED_SQL
    assert "s.deleted_at IS NULL" in _UNSCORED_SQL
    assert "u.deleted_at IS NULL" in _UNSCORED_SQL


def test_retry_leaves_recent_and_old_interviews_alone() -> None:
    """Recent: the live call may still land. Old: a results email about an
    interview from last quarter is worse than none."""
    from app.reconciliation import _SCORECARD_GRACE, _SCORECARD_LOOKBACK, _UNSCORED_SQL

    assert "s.completed_at > :floor" in _UNSCORED_SQL
    assert "s.completed_at <= :settled" in _UNSCORED_SQL
    assert timedelta(minutes=10) <= _SCORECARD_GRACE
    assert timedelta(days=7) >= _SCORECARD_LOOKBACK


def _session_row(**over: object) -> dict:
    row = {
        "id": uuid.uuid4(), "language": "hi", "job_title": "Staff Nurse", "level": "junior",
        "jd_text": "Ward care", "department": None, "company_name": None,
        "interview_type": "screening", "current_round_id": None,
    }
    return {**row, **over}


def _scorecard_db(rows: list[dict], turns: list[tuple[str, str]]) -> AsyncMock:
    db = _db()
    db.execute.side_effect = [
        MagicMock(mappings=MagicMock(return_value=MagicMock(all=MagicMock(return_value=rows)))),
        MagicMock(all=MagicMock(return_value=turns)),
    ] + [MagicMock() for _ in range(10)]
    return db


@pytest.mark.asyncio
async def test_a_retried_interview_is_sent_exactly_as_the_worker_would_send_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.reconciliation as rec

    row = _session_row()
    db = _scorecard_db([row], [("interviewer", "Tell me about triage."),
                               ("candidate", "I assess by urgency.")])
    sent: list[dict] = []

    async def _score(payload: dict) -> str:
        sent.append(payload)
        return "created"

    async def _profile(_db: object, _row: object) -> dict:
        return {"profile_id": "p1"}

    monkeypatch.setattr(rec, "score_interview_remote", _score)
    monkeypatch.setattr(rec, "_role_profile_for", _profile)
    result = rec.PassResult()
    await rec._scorecard_pass(db, result)

    assert result.interviews_scored == 1
    p = sent[0]
    assert p["session_id"] == str(row["id"])
    assert p["turns"] == [{"role": "ai", "text": "Tell me about triage."},
                          {"role": "user", "text": "I assess by urgency."}]
    assert p["language"] == "hi"
    assert p["experience_level"] == "entry"  # unknown levels normalise like the worker's
    assert p["role_profile"] == {"profile_id": "p1"}


@pytest.mark.asyncio
async def test_a_session_scored_meanwhile_is_not_counted_twice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """409 from the scorer is UNIQUE(session_id) — the live call landed first.
    That is success for the retry, not a failure, and not a new scorecard."""
    import app.reconciliation as rec

    db = _scorecard_db([_session_row()], [("candidate", "a"), ("candidate", "b")])

    async def _exists(_p: dict) -> str:
        return "exists"

    async def _profile(_db: object, _row: object) -> None:
        return None

    monkeypatch.setattr(rec, "score_interview_remote", _exists)
    monkeypatch.setattr(rec, "_role_profile_for", _profile)
    result = rec.PassResult()
    await rec._scorecard_pass(db, result)

    assert result.interviews_scored == 0
    assert result.failed == 0


@pytest.mark.asyncio
async def test_a_scoring_failure_backs_off_with_the_lower_attempt_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every attempt that reaches Gemini is spend; interviews get fewer tries."""
    import app.reconciliation as rec

    db = _scorecard_db([_session_row()], [("candidate", "a"), ("candidate", "b")])
    caps: list[int] = []

    async def _down(_p: dict) -> str:
        raise RuntimeError("scorer 502")

    async def _fail(_db: object, _k: str, _r: object, _e: str, *, max_attempts: int) -> bool:
        caps.append(max_attempts)
        return False

    async def _profile(_db: object, _row: object) -> None:
        return None

    monkeypatch.setattr(rec, "score_interview_remote", _down)
    monkeypatch.setattr(rec, "_record_failure", _fail)
    monkeypatch.setattr(rec, "_role_profile_for", _profile)
    result = rec.PassResult()
    await rec._scorecard_pass(db, result)

    assert result.failed == 1
    assert caps == [rec.SCORECARD_MAX_ATTEMPTS]
    assert rec.SCORECARD_MAX_ATTEMPTS < rec.MAX_ATTEMPTS


@pytest.mark.asyncio
async def test_a_workflow_interview_is_rescored_against_its_frozen_rubric(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The retry grades against what the round promised, like the live worker."""
    import app.reconciliation as rec
    import app.workflow_runner as wr

    round_id = uuid.uuid4()
    frozen = MagicMock(model_dump=MagicMock(return_value={"profile_id": "frozen"}))
    seen: list[object] = []

    async def _frozen(_db: object, *, round_id: object, job_title: str) -> object:
        seen.append(round_id)
        return frozen

    monkeypatch.setattr(wr, "frozen_rubric_for_round", _frozen)
    got = await rec._role_profile_for(_db(), _session_row(current_round_id=round_id))

    assert seen == [round_id]
    assert got == {"profile_id": "frozen"}


@pytest.mark.asyncio
async def test_a_practice_interview_is_rescored_against_the_deterministic_baseline() -> None:
    """No LLM call to rebuild a rubric: the taxonomy baseline is what the worker
    itself falls back to when Gemini is unavailable."""
    from app.reconciliation import _role_profile_for

    got = await _role_profile_for(_db(), _session_row())

    assert got is not None
    assert got["source"] == "taxonomy"  # no LLM contributed
    assert got["domain_family"] == "healthcare_nursing"  # role-aware, not generic


def _pdf_db(ids: list[uuid.UUID]) -> AsyncMock:
    db = _db()
    db.execute.side_effect = [MagicMock(all=MagicMock(return_value=[(i,) for i in ids]))] + [
        MagicMock() for _ in range(10)
    ]
    return db


@pytest.mark.asyncio
async def test_a_pdf_that_can_never_render_is_parked_not_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No candidate name, or pending erasure: a retry would get the same answer."""
    import app.reconciliation as rec

    scid = uuid.uuid4()
    parked: list[tuple] = []

    async def _render(_id: str) -> dict:
        return {"status": "not_applicable", "reason": "no_candidate_name"}

    async def _park(_db: object, kind: str, ref: object, reason: str) -> None:
        parked.append((kind, ref, reason))

    monkeypatch.setattr(rec, "render_scorecard_pdf_remote", _render)
    monkeypatch.setattr(rec, "_park", _park)
    result = rec.PassResult()
    await rec._pdf_pass(_pdf_db([scid]), result)

    assert parked == [(rec.KIND_PDF, scid, "not_applicable: no_candidate_name")]
    assert result.pdfs_rendered == 0 and result.failed == 0


@pytest.mark.asyncio
async def test_a_pdf_render_failure_is_retried_and_the_pass_continues(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.reconciliation as rec
    from app.scoring_client import InterviewScoreError

    bad, good = uuid.uuid4(), uuid.uuid4()

    async def _render(scid: str) -> dict:
        if scid == str(bad):
            raise InterviewScoreError("storage down")
        return {"status": "rendered"}

    failures: list[object] = []

    async def _fail(_db: object, _k: str, ref: object, _e: str, **_: object) -> bool:
        failures.append(ref)
        return False

    monkeypatch.setattr(rec, "render_scorecard_pdf_remote", _render)
    monkeypatch.setattr(rec, "_record_failure", _fail)
    result = rec.PassResult()
    await rec._pdf_pass(_pdf_db([bad, good]), result)

    assert failures == [bad]
    assert result.pdfs_rendered == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["_scorecard_pass", "_pdf_pass"])
async def test_an_unavailable_service_stops_the_pass_without_charging_anyone(
    monkeypatch: pytest.MonkeyPatch, stage: str,
) -> None:
    """Storage not configured, or feedback_billing down: a condition of the
    service. Charging each row an attempt would park every interview and every
    scorecard after an hour-long outage — and they would stay parked."""
    import app.reconciliation as rec
    from app.scoring_client import ScoringServiceUnavailableError

    calls: list[str] = []

    async def _down(*_: object) -> object:
        calls.append("call")
        raise ScoringServiceUnavailableError("HTTP 503: storage not configured")

    async def _fail(*_: object, **__: object) -> bool:
        raise AssertionError("no row may be charged for a service-level outage")

    async def _profile(_db: object, _row: object) -> None:
        return None

    monkeypatch.setattr(rec, "score_interview_remote", _down)
    monkeypatch.setattr(rec, "render_scorecard_pdf_remote", _down)
    monkeypatch.setattr(rec, "_record_failure", _fail)
    monkeypatch.setattr(rec, "_role_profile_for", _profile)

    db = (_scorecard_db([_session_row(), _session_row()],
                        [("candidate", "a"), ("candidate", "b")])
          if stage == "_scorecard_pass" else _pdf_db([uuid.uuid4(), uuid.uuid4()]))
    result = rec.PassResult()
    await getattr(rec, stage)(db, result)

    assert calls == ["call"], "the pass must stop at the first service-level failure"
    assert result.failed == 0


class _Transport:
    """Serve one canned response (or raise) for httpx calls inside scoring_client."""

    def __init__(self, status: int | None, body: object = None) -> None:
        self.status, self.body = status, body

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import httpx

        import app.scoring_client as sc

        def _handler(request: httpx.Request) -> httpx.Response:
            if self.status is None:
                raise httpx.ConnectError("refused", request=request)
            return httpx.Response(self.status, json=self.body)

        real = httpx.AsyncClient

        def _client(**kw: object) -> httpx.AsyncClient:
            return real(transport=httpx.MockTransport(_handler), **kw)

        monkeypatch.setattr(sc.httpx, "AsyncClient", _client)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "expect"),
    [(201, "created"), (409, "exists"), (None, "unavailable"), (503, "unavailable"),
     (401, "unavailable"), (502, "item_failed"), (422, "item_failed")],
)
async def test_score_interview_remote_classifies_each_answer(
    monkeypatch: pytest.MonkeyPatch, status: int | None, expect: str,
) -> None:
    """502 is Gemini rejecting THIS transcript — charge it. Unreachable, 401/403
    and 503 would fail for every session alike — don't."""
    from app.scoring_client import (
        InterviewScoreError,
        ScoringServiceUnavailableError,
        score_interview_remote,
    )

    _Transport(status, {"detail": "x"}).install(monkeypatch)
    try:
        got = await score_interview_remote({"session_id": "s"})
    except ScoringServiceUnavailableError:
        got = "unavailable"
    except InterviewScoreError:
        got = "item_failed"
    assert got == expect


@pytest.mark.asyncio
async def test_pdf_storage_not_configured_is_a_service_condition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.scoring_client import ScoringServiceUnavailableError, render_scorecard_pdf_remote

    _Transport(503, {"detail": "Scorecard storage is not configured."}).install(monkeypatch)
    with pytest.raises(ScoringServiceUnavailableError):
        await render_scorecard_pdf_remote(str(uuid.uuid4()))


def test_pdfs_still_being_rendered_by_the_live_task_are_left_alone() -> None:
    from app.reconciliation import _MISSING_PDF_SQL, _PDF_GRACE

    assert "sc.report_pdf_key IS NULL" in _MISSING_PDF_SQL
    assert "sc.created_at <= :settled" in _MISSING_PDF_SQL
    assert timedelta(minutes=5) <= _PDF_GRACE


@pytest.mark.asyncio
async def test_one_failing_pass_does_not_cost_the_others_their_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.reconciliation as rec

    ran: list[str] = []

    def _stage(name: str, boom: bool = False):  # noqa: ANN202
        async def _fn(_db: object, _r: object) -> None:
            ran.append(name)
            if boom:
                raise RuntimeError("bad sql")
        return _fn

    async def _nothing(_db: object) -> dict:
        return {}

    monkeypatch.setattr(rec, "_score_pass", _stage("score", boom=True))
    monkeypatch.setattr(rec, "_embed_pass", _stage("embed"))
    monkeypatch.setattr(rec, "_scorecard_pass", _stage("scorecard", boom=True))
    monkeypatch.setattr(rec, "_pdf_pass", _stage("pdf"))
    monkeypatch.setattr(rec, "_outstanding", _nothing)

    await rec.run_once(_FakeFactory(_db()))  # type: ignore[arg-type]
    assert ran == ["score", "embed", "scorecard", "pdf"]


# ===========================================================================
# A2 — reminder sweep
# ===========================================================================
def test_deadline_is_rendered_in_ist() -> None:
    """Every candidate is in India; a UTC timestamp invites a missed deadline."""
    from app.reminders import _fmt

    got = _fmt(datetime(2026, 9, 3, 12, 30, tzinfo=UTC))
    assert "06:00 PM IST" in got
    assert "03 Sep 2026" in got


def test_fmt_passes_through_none() -> None:
    from app.reminders import _fmt

    assert _fmt(None) is None


@pytest.mark.asyncio
async def test_exam_reminder_dedupe_key_names_the_window_not_the_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A key derived from 'now' would re-send on every sweep."""
    import app.reminders as rem

    row = {
        "id": uuid.uuid4(), "expires_at": datetime.now(tz=UTC) + timedelta(hours=2),
        "scheduled_at": None, "applicant_id": uuid.uuid4(), "full_name": "Priya",
        "email": "p@example.com", "user_id": None, "exam_title": "Aptitude",
        "round_title": "Round 1", "company_id": uuid.uuid4(),
    }
    db = _db()
    db.execute.return_value = MagicMock(mappings=MagicMock(return_value=MagicMock(
        all=MagicMock(return_value=[row]))))

    keys: list[str] = []

    async def _enqueue(_db: object, **kw: object) -> object:
        keys.append(str(kw["dedupe_key"]))
        return object()

    monkeypatch.setattr(rem, "enqueue_email", _enqueue)
    await rem._exam_reminders(db, rem.SweepResult())

    assert keys == [f"exam_reminder:{row['id']}:24h", f"exam_reminder:{row['id']}:1h"]


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["_exam_reminders", "_interview_reminders"])
async def test_reminder_windows_are_bands_not_nested_ranges(stage: str) -> None:
    """A deadline forty minutes out used to match both windows, so the candidate
    got two emails in the same minute and one of them said "closes tomorrow".
    The 24h window now stops where the 1h window starts."""
    import app.reminders as rem

    db = _db()
    db.execute.return_value = MagicMock(mappings=MagicMock(return_value=MagicMock(
        all=MagicMock(return_value=[]))))
    before = datetime.now(tz=UTC)
    await getattr(rem, stage)(db, rem.SweepResult())

    day, hour = (c.args[1] for c in db.execute.call_args_list)
    # 24h band: (now+1h, now+24h]; 1h band: (now, now+1h]. Adjacent, not nested.
    assert day["near"] == hour["horizon"]
    assert day["horizon"] - day["near"] == timedelta(hours=23)
    assert hour["horizon"] - hour["near"] == timedelta(hours=1)
    assert abs(hour["near"] - before) < timedelta(seconds=5)


def test_reminder_queries_bound_both_edges_of_the_band() -> None:
    from app.reminders import _EXAM_DUE_SQL, _INTERVIEW_DUE_SQL

    assert "asg.expires_at > :near" in _EXAM_DUE_SQL
    assert "COALESCE(inv.scheduled_at, inv.expires_at) > :near" in _INTERVIEW_DUE_SQL


def test_the_workflow_reminder_switch_is_honoured() -> None:
    """"Remind candidates" in the workflow builder was stored and never read.
    Every candidate-facing reminder query now joins through the enrolment to
    the workflow and respects it; rows with no workflow keep the default (on)."""
    from app.reminders import _EXAM_DUE_SQL, _INTERVIEW_DUE_SQL, _LAPSED_SQL, _REMINDERS_ON

    assert _REMINDERS_ON == "COALESCE(wf.reminders_enabled, true)"
    for sql in (_EXAM_DUE_SQL, _INTERVIEW_DUE_SQL, _LAPSED_SQL):
        assert _REMINDERS_ON in sql
        assert "LEFT JOIN workflows" in sql  # LEFT: ad-hoc invites have no workflow


def _lapsed(**over: object) -> dict:
    row = {
        "kind": "exam", "id": uuid.uuid4(), "expires_at": datetime.now(tz=UTC),
        "company_id": uuid.uuid4(), "owner_user_id": uuid.uuid4(),
        "full_name": "Anita", "email": "anita@example.com", "user_id": None,
        "what": "Technical Round", "mail_candidate": True,
    }
    return {**row, **over}


def _rows_db(rows: list[dict]) -> AsyncMock:
    db = _db()
    db.execute.return_value = MagicMock(mappings=MagicMock(return_value=MagicMock(
        all=MagicMock(return_value=rows))))
    return db


@pytest.mark.asyncio
async def test_hr_hears_about_a_lapse_even_when_the_candidate_has_no_email(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The owner used to be told only if the candidate email was staged, so a
    candidate with no address lapsed in silence on both sides."""
    import app.reminders as rem

    row = _lapsed(email=None, mail_candidate=None)
    emails: list[dict] = []

    async def _enqueue(_db: object, **kw: object) -> object:
        emails.append(kw)
        return object()

    monkeypatch.setattr(rem, "enqueue_email", _enqueue)
    sent = _capture_notifications(monkeypatch, rem)
    await rem._expiry_notices(_rows_db([row]), rem.SweepResult())

    assert emails == []
    assert len(sent) == 1
    assert sent[0]["user_id"] == row["owner_user_id"]
    assert sent[0]["kind"] == "link_expired"
    assert sent[0]["dedupe_key"] == f"link_expired:exam:{row['id']}"


@pytest.mark.asyncio
async def test_hr_is_told_even_when_the_candidate_email_was_already_staged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The two obligations are independent: an email that dedupes (None) no
    longer short-circuits the owner's notification. The notification's own key
    is what stops a repeat."""
    import app.reminders as rem

    async def _deduped(_db: object, **_: object) -> None:
        return None

    monkeypatch.setattr(rem, "enqueue_email", _deduped)
    sent = _capture_notifications(monkeypatch, rem)
    result = rem.SweepResult()
    await rem._expiry_notices(_rows_db([_lapsed()]), result)

    assert result.expiry_notices == 0
    assert len(sent) == 1


@pytest.mark.asyncio
async def test_reminders_off_skips_the_candidate_email_but_still_tells_hr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The switch is "Remind candidates" — it silences the candidate side. The
    owner still needs to know the link lapsed; that is work, not a reminder."""
    import app.reminders as rem

    emails: list[dict] = []

    async def _enqueue(_db: object, **kw: object) -> object:
        emails.append(kw)
        return object()

    monkeypatch.setattr(rem, "enqueue_email", _enqueue)
    sent = _capture_notifications(monkeypatch, rem)
    await rem._expiry_notices(_rows_db([_lapsed(mail_candidate=False)]), rem.SweepResult())

    assert emails == []
    assert len(sent) == 1


def test_a_workflow_issued_link_has_an_owner() -> None:
    """Links a workflow issues carry no creator, so their lapses reached nobody.
    The owner falls back to the workflow's owner."""
    from app.reminders import _LAPSED_SQL

    assert "COALESCE(asg.created_by_user_id, wf.created_by_user_id)" in _LAPSED_SQL
    assert "COALESCE(inv.created_by_user_id, wf.created_by_user_id)" in _LAPSED_SQL


def test_a_fully_handled_lapse_drops_out_of_the_batch() -> None:
    """A row with nothing left to send must not come back every sweep — with no
    key to find, a candidate with no address used to take a batch slot forever."""
    from app.reminders import _LAPSED_SQL

    assert "n.dedupe_key = 'link_expired:'" in _LAPSED_SQL
    assert "ee.dedupe_key = 'expiry_notice:'" in _LAPSED_SQL
    assert "ORDER BY expires_at" in _LAPSED_SQL
    # Mirrors enqueue_email's own validity check, or an unsendable address
    # would count as an email still owed.
    assert "a.email LIKE '%@%'" in _LAPSED_SQL


@pytest.mark.asyncio
async def test_rescheduling_rearms_both_reminder_windows() -> None:
    """The keys name the invite and the window, so reminders already spent on
    the old slot blocked the new one. Retiring them lets the next sweep remind
    about the time the candidate actually has to turn up."""
    from app.reminders import interview_reminder_key, rearm_interview_reminders

    inv = uuid.uuid4()
    db = _db()
    await rearm_interview_reminders(db, inv)

    sql, params = db.execute.call_args.args
    assert "UPDATE email_events" in str(sql)
    assert ":superseded:" in str(sql)
    assert set(params.values()) == {
        interview_reminder_key(inv, "24h"),
        interview_reminder_key(inv, "1h"),
    }


def test_reschedule_rearms_only_when_the_slot_changes() -> None:
    import inspect

    from app.routers.hr_interviews import reschedule_invite

    src = inspect.getsource(reschedule_invite)
    assert "if inv.scheduled_at != body.scheduled_at:" in src
    assert "await rearm_interview_reminders(db, inv.id)" in src
    # Re-armed before the new time is written, while it can still be compared.
    assert src.index("rearm_interview_reminders") < src.index(
        "inv.scheduled_at = body.scheduled_at"
    )


# ===========================================================================
# Notification dedupe
# ===========================================================================
@pytest.mark.asyncio
async def test_notification_without_a_key_is_staged_as_before() -> None:
    from app.notifications_util import create_notification

    db = _db()
    db.add = MagicMock()
    assert await create_notification(db, user_id=uuid.uuid4(), kind="welcome", title="Hi")
    db.add.assert_called_once()
    db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_notification_with_a_key_inserts_on_conflict_do_nothing() -> None:
    from sqlalchemy.dialects import postgresql

    from app.notifications_util import create_notification

    db = _db()
    db.execute.return_value = MagicMock(first=MagicMock(return_value=None))  # conflict
    staged = await create_notification(
        db, user_id=uuid.uuid4(), kind="bulk_upload", title="Done", dedupe_key="bulk_upload:x"
    )

    assert staged is False, "a conflicting key must report nothing staged"
    compiled = str(db.execute.call_args.args[0].compile(dialect=postgresql.dialect()))
    assert "ON CONFLICT (dedupe_key) WHERE dedupe_key IS NOT NULL DO NOTHING" in compiled


@pytest.mark.asyncio
async def test_notification_with_no_recipient_is_a_no_op() -> None:
    from app.notifications_util import create_notification

    db = _db()
    db.add = MagicMock()
    assert await create_notification(
        db, user_id=None, kind="x", title="y", dedupe_key="k"
    ) is False
    db.add.assert_not_called()
    db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_failing_stage_does_not_sink_the_sweep(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.reminders as rem

    db = _db()
    factory = _FakeFactory(db)
    order: list[str] = []

    async def _boom(_db: object, _r: object) -> None:
        order.append("exam")
        raise RuntimeError("bad sql")

    async def _ok(_db: object, r: object) -> None:
        order.append("results")
        r.results_emails += 1

    # Every stage is patched, including _interview_completed. It used to be
    # left out simply because it did not exist when this was written, so it ran
    # for real against a mock session — passing by luck rather than by intent,
    # and quietly making this a test of four stages plus one accident.
    for stage in (
        "_interview_reminders",
        "_expiry_notices",
        "_results_ready",
        "_interview_completed",
        "_workflow_results",
    ):
        monkeypatch.setattr(rem, stage, _ok)
    monkeypatch.setattr(rem, "_exam_reminders", _boom)

    result = await rem.run_once(factory)  # type: ignore[arg-type]
    assert "exam" in order and order.count("results") == 5
    assert result.results_emails == 5


# ===========================================================================
# A6 — scheduled job bookkeeping
# ===========================================================================
@pytest.mark.asyncio
async def test_job_runs_when_the_claim_is_won() -> None:
    from app.scheduling import run_scheduled_job

    db = _db(scalar=1)  # claim won
    ran = {"v": False}

    async def _fn() -> None:
        ran["v"] = True

    did = await run_scheduled_job(
        _FakeFactory(db), job_id="j", interval=timedelta(days=1), fn=_fn  # type: ignore[arg-type]
    )
    assert did is True and ran["v"] is True


@pytest.mark.asyncio
async def test_job_is_skipped_when_another_instance_holds_the_claim() -> None:
    from app.scheduling import run_scheduled_job

    db = _db(scalar=None)  # claim lost / not due
    ran = {"v": False}

    async def _fn() -> None:
        ran["v"] = True

    did = await run_scheduled_job(
        _FakeFactory(db), job_id="j", interval=timedelta(days=1), fn=_fn  # type: ignore[arg-type]
    )
    assert did is False and ran["v"] is False


@pytest.mark.asyncio
async def test_a_raising_job_is_recorded_not_propagated() -> None:
    """APScheduler drops a job that raises — one bad night must not end the purge."""
    from app.scheduling import run_scheduled_job

    db = _db(scalar=1)

    async def _fn() -> None:
        raise RuntimeError("db hiccup")

    did = await run_scheduled_job(
        _FakeFactory(db), job_id="j", interval=timedelta(days=1), fn=_fn  # type: ignore[arg-type]
    )
    assert did is True, "the run happened and must be recorded as such"
    sql = " ".join(str(c.args[0]) for c in db.execute.call_args_list)
    assert "last_status" in sql
    params = [c.args[1] for c in db.execute.call_args_list if len(c.args) > 1]
    assert any(p.get("status") == "error" for p in params)
    assert any("db hiccup" in str(p.get("err", "")) for p in params)


@pytest.mark.asyncio
async def test_catchup_survives_one_broken_job() -> None:
    """A job that explodes during catch-up must not prevent the service booting."""
    from app.scheduling import run_overdue_jobs_on_startup

    db = _db(scalar=1)
    seen: list[str] = []

    async def _bad() -> None:
        raise RuntimeError("nope")

    async def _good() -> None:
        seen.append("good")

    await run_overdue_jobs_on_startup(
        _FakeFactory(db),  # type: ignore[arg-type]
        [("a", timedelta(days=1), _bad), ("b", timedelta(days=1), _good)],
    )
    assert seen == ["good"]


def _claim_params(db: AsyncMock) -> dict:
    return next(c.args[1] for c in db.scalar.call_args_list if "INSERT INTO" in str(c.args[0]))


@pytest.mark.asyncio
async def test_catchup_claims_only_a_window_that_was_really_missed() -> None:
    """With the scheduler's own early grace, a check every fifteen minutes would
    claim each night's run shortly before its hour, and the job would creep
    earlier every day. Catch-up waits until the window is hours past due."""
    from app.scheduling import _CATCHUP_SLACK, _OVERDUE_GRACE, _claim

    day = timedelta(days=1)
    cron_db, catch_db = _db(scalar=1), _db(scalar=1)
    before = datetime.now(tz=UTC)
    await _claim(cron_db, "j", interval=day)
    await _claim(catch_db, "j", interval=day, catchup=True)

    cron_due = _claim_params(cron_db)["due_before"]
    catch_due = _claim_params(catch_db)["due_before"]
    assert abs(cron_due - (before - day + _OVERDUE_GRACE)) < timedelta(seconds=5)
    assert abs(catch_due - (before - day - _CATCHUP_SLACK)) < timedelta(seconds=5)
    assert catch_due < cron_due


@pytest.mark.asyncio
async def test_a_failed_run_is_retried_within_the_hour_not_the_next_night() -> None:
    from app.scheduling import _ERROR_RETRY_AFTER, _claim

    db = _db(scalar=1)
    before = datetime.now(tz=UTC)
    await _claim(db, "j", interval=timedelta(days=1))

    sql = next(str(c.args[0]) for c in db.scalar.call_args_list)
    assert "last_status = 'error'" in sql
    assert "last_finished_at <= :retry_before" in sql
    assert timedelta(hours=1) >= _ERROR_RETRY_AFTER
    assert abs(_claim_params(db)["retry_before"] - (before - _ERROR_RETRY_AFTER)) < timedelta(
        seconds=5
    )


@pytest.mark.asyncio
async def test_the_catchup_trigger_reaches_the_claim(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.scheduling as sch

    seen: list[bool] = []

    async def _claim(_db: object, _jid: str, *, interval: timedelta, catchup: bool) -> bool:
        seen.append(catchup)
        return False

    monkeypatch.setattr(sch, "_claim", _claim)

    async def _fn() -> None:
        return None

    await sch.run_overdue_jobs_on_startup(
        _FakeFactory(_db()), [("j", timedelta(days=1), _fn)]  # type: ignore[arg-type]
    )
    await sch.run_scheduled_job(
        _FakeFactory(_db()), job_id="j", interval=timedelta(days=1), fn=_fn  # type: ignore[arg-type]
    )
    assert seen == [True, False]


@pytest.mark.asyncio
async def test_every_run_is_written_to_history() -> None:
    from app.scheduling import run_scheduled_job

    db = _db(scalar=1)

    async def _fn() -> None:
        return None

    await run_scheduled_job(
        _FakeFactory(db), job_id="j", interval=timedelta(days=1), fn=_fn,  # type: ignore[arg-type]
        trigger="catchup",
    )
    logged = [c.args[1] for c in db.execute.call_args_list
              if "INSERT INTO scheduled_job_run_log" in str(c.args[0])]
    assert len(logged) == 1
    assert logged[0]["status"] == "ok" and logged[0]["trigger"] == "catchup"


def test_contact_details_are_masked_before_an_error_is_stored() -> None:
    """A unique-violation quotes the duplicate row. These tables are declared as
    holding no personal data, so an email or phone number must not land there."""
    from app.scheduling import _scrub

    raw = ('IntegrityError: Key (email)=(priya.sharma@example.co.in) already exists; '
           'phone +91 98765 43210')
    out = _scrub(raw) or ""
    assert "priya" not in out and "98765" not in out
    assert "<email>" in out and "<number>" in out
    assert "IntegrityError" in out  # still useful to the operator
    assert _scrub(None) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(("error", "history_rows"), [(None, 0), ("results: RuntimeError", 1)])
async def test_a_loop_pass_updates_its_summary_and_logs_only_failures(
    error: str | None, history_rows: int,
) -> None:
    """Every pass refreshes the summary row — a loop that has died shows up as a
    stale timestamp. History takes only failures, or it fills with "fine"."""
    from app.scheduling import record_loop_pass

    db = _db()
    await record_loop_pass(
        _FakeFactory(db), "reminders_sweep",  # type: ignore[arg-type]
        started_at=datetime.now(tz=UTC), error=error,
    )
    sqls = [str(c.args[0]) for c in db.execute.call_args_list]
    assert sum("INSERT INTO scheduled_job_runs" in s for s in sqls) == 1
    assert sum("INSERT INTO scheduled_job_run_log" in s for s in sqls) == history_rows
    db.commit.assert_awaited()


def test_startup_no_longer_waits_for_the_catchup() -> None:
    """It used to be awaited in lifespan, holding the service unready until the
    whole retention purge finished — and it only ever ran on a restart."""
    import inspect

    from app import main

    src = inspect.getsource(main.lifespan)
    assert "await run_overdue_jobs_on_startup" not in src
    assert "start_catchup(_factory, _catchup_jobs)" in src
    assert "await stop_catchup()" in src


@pytest.mark.asyncio
async def test_start_catchup_returns_at_once_and_stops_cleanly() -> None:
    import app.scheduling as sch

    async def _fn() -> None:
        return None

    sch.start_catchup(_FakeFactory(_db()), [("j", timedelta(days=1), _fn)])  # type: ignore[arg-type]
    try:
        assert sch._catchup_task is not None and not sch._catchup_task.done()
    finally:
        await sch.stop_catchup()
    assert sch._catchup_task is None


@pytest.mark.asyncio
async def test_both_interval_loops_report_their_failed_stages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stage that fails is swallowed so the rest can run — which used to mean
    the failure reached nothing but a log line."""
    import app.reminders as rem

    async def _boom(_db: object, _r: object) -> None:
        raise RuntimeError("bad sql")

    async def _ok(_db: object, _r: object) -> None:
        return None

    for stage in ("_exam_reminders", "_interview_reminders", "_results_ready",
                  "_interview_completed", "_workflow_results"):
        monkeypatch.setattr(rem, stage, _ok)
    monkeypatch.setattr(rem, "_expiry_notices", _boom)

    result = await rem.run_once(_FakeFactory(_db()))  # type: ignore[arg-type]
    assert result.failed_stages == ["expiry: RuntimeError: bad sql"]


@pytest.mark.asyncio
async def test_the_ops_endpoint_flags_a_job_that_went_quiet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A dead loop does not report failures; it stops reporting. Overdue is
    judged from the last finish, against each job's own expected gap."""
    import app.routers.admin_hr as admin

    now = datetime.now(tz=UTC)

    async def _status(_f: object) -> list[dict]:
        return [
            {"job_id": "reminders_sweep", "last_finished_at": (now - timedelta(hours=2)).isoformat()},
            {"job_id": "reconciliation_loop", "last_finished_at": (now - timedelta(minutes=5)).isoformat()},
            {"job_id": "retention_purge", "last_finished_at": None},
            {"job_id": "something_new", "last_finished_at": None},
        ]

    monkeypatch.setattr(admin, "job_status", _status)
    monkeypatch.setattr(admin, "get_session_factory", lambda: None)
    body = await admin.scheduled_jobs(current_user=MagicMock())
    overdue = {j["job_id"]: j["overdue"] for j in body["jobs"]}
    assert overdue == {"reminders_sweep": True, "reconciliation_loop": False,
                       "retention_purge": True, "something_new": False}


# ===========================================================================
# A4/E5 — one "upload complete" notification per bulk batch
# ===========================================================================
def _counts(total: int, outstanding: int, unreadable: int = 0) -> MagicMock:
    """What _BATCH_COUNTS_SQL returns, as the mocked execute() result."""
    row = {"total": total, "outstanding": outstanding, "unreadable": unreadable}
    return MagicMock(mappings=MagicMock(return_value=MagicMock(
        first=MagicMock(return_value=row))))


def _capture_notifications(
    monkeypatch: pytest.MonkeyPatch, module: object
) -> list[dict]:
    sent: list[dict] = []

    async def _notify(_db: object, **kw: object) -> bool:
        sent.append(kw)
        return True

    monkeypatch.setattr(module, "create_notification", _notify)
    return sent


@pytest.mark.asyncio
async def test_batch_notification_fires_only_when_the_last_row_lands(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nothing outstanding means this row finished the batch."""
    import app.reconciliation as rec

    sent = _capture_notifications(monkeypatch, rec)
    db = _db()
    db.execute.return_value = _counts(total=25, outstanding=0)
    batch = uuid.uuid4()

    fired = await rec._notify_batch_done(db, batch, uuid.uuid4())

    assert fired is True
    assert len(sent) == 1
    assert sent[0]["kind"] == "bulk_upload"
    assert sent[0]["dedupe_key"] == f"bulk_upload:{batch}"
    assert "25 resumes have been read and scored" in sent[0]["body"]


@pytest.mark.asyncio
async def test_no_notification_while_rows_are_still_being_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The 24 rows before the last one must stay silent.

    This is the whole reason the batch id exists: without it every scored row
    looks equally like "the upload finished".
    """
    import app.reconciliation as rec

    sent = _capture_notifications(monkeypatch, rec)
    db = _db()
    db.execute.return_value = _counts(total=25, outstanding=7)

    assert await rec._notify_batch_done(db, uuid.uuid4(), uuid.uuid4()) is False
    assert sent == []


@pytest.mark.asyncio
async def test_an_unreadable_resume_does_not_hold_the_batch_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One parked or empty PDF out of twenty-five used to keep the batch
    "pending" for good, so the upload was never reported finished. The counts
    now separate rows the loop will still finish from rows it never will, and
    the message says how many could not be read rather than claiming all."""
    import app.reconciliation as rec

    sent = _capture_notifications(monkeypatch, rec)
    db = _db()
    db.execute.return_value = _counts(total=25, outstanding=0, unreadable=2)

    assert await rec._notify_batch_done(db, uuid.uuid4(), uuid.uuid4()) is True
    assert "23 of 25" in sent[0]["body"]
    assert "2 could not be read" in sent[0]["body"]


def test_stuck_rows_are_excluded_from_outstanding() -> None:
    """The three ways a pending row is never going to be finished by this loop."""
    from app.reconciliation import _BATCH_COUNTS_SQL

    sql = _BATCH_COUNTS_SQL
    assert "rs.gave_up_at IS NOT NULL" in sql  # parked after MAX_ATTEMPTS
    assert "a.ats_overall IS NOT NULL" in sql  # scored by the manual rescore
    assert "length(trim(a.resume_text)) = 0" in sql  # nothing to score
    assert "AND NOT st.stuck) AS outstanding" in sql
    assert "rs.kind = :kind_ats" in sql  # a bound parameter, not interpolated


@pytest.mark.asyncio
async def test_a_row_given_up_on_can_finish_its_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the last outstanding row is the one that fails for good, nothing else
    will ever come along to announce the batch — so the give-up must."""
    import app.reconciliation as rec

    aid, batch, uploader = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    db = _db()
    db.execute.return_value = MagicMock(all=MagicMock(return_value=[(aid,)]))
    db.get = AsyncMock(return_value=MagicMock(
        id=aid, resume_text="cv", target_job_title="Dev", target_level="mid",
        target_jd_text=None, created_by_user_id=uploader, upload_batch_id=batch,
    ))

    async def _score(**_: object) -> dict[str, int]:
        raise RuntimeError("unreadable")

    async def _gave_up(*_: object) -> bool:
        return True

    announced: list[tuple] = []

    async def _notify_batch(_db: object, b: object, u: object) -> bool:
        announced.append((b, u))
        return True

    monkeypatch.setattr(rec, "score_resume_remote", _score)
    monkeypatch.setattr(rec, "_record_failure", _gave_up)
    monkeypatch.setattr(rec, "_notify_batch_done", _notify_batch)

    result = rec.PassResult()
    await rec._score_pass(db, result)

    assert result.gave_up == 1
    assert announced == [(batch, uploader)]
    assert result.batches_finished == 1


@pytest.mark.asyncio
async def test_rows_outside_a_batch_never_notify() -> None:
    """A single upload or a public application has no batch to complete."""
    from app.reconciliation import _notify_batch_done

    db = _db()
    assert await _notify_batch_done(db, None, uuid.uuid4()) is False
    db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_uploader_means_nobody_to_tell() -> None:
    from app.reconciliation import _notify_batch_done

    db = _db()
    assert await _notify_batch_done(db, uuid.uuid4(), None) is False
    db.execute.assert_not_awaited()


# ===========================================================================
# Telling people the interview is over
# ===========================================================================
# Completion had no announcer. The invite's consumed -> completed flip, and the
# notification with it, lived inside GET /hr/interviews — so HR learned that a
# candidate had finished only by opening that page, and the invite sat at
# 'consumed' until someone did. A read path was also, quietly, a writer.
@pytest.mark.asyncio
async def test_completion_notifies_hr_without_anyone_opening_a_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.reminders as rem

    row = {
        "id": uuid.uuid4(), "company_id": uuid.uuid4(),
        "created_by_user_id": uuid.uuid4(),
        "full_name": "Priya", "job_title": "Backend Engineer",
    }
    db = _db()
    # One mock serves both the SELECT (mappings) and the UPDATE ... RETURNING (first).
    db.execute.return_value = MagicMock(
        first=MagicMock(return_value=(row["id"],)),
        mappings=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[row]))),
    )

    sent = _capture_notifications(monkeypatch, rem)
    result = rem.SweepResult()
    await rem._interview_completed(db, result)

    assert result.completions == 1
    assert sent[0]["user_id"] == row["created_by_user_id"]
    assert sent[0]["kind"] == "interview_completed"
    assert sent[0]["link"] == "/hr/interviews"
    assert sent[0]["dedupe_key"] == f"interview_completed:{row['id']}"


@pytest.mark.asyncio
async def test_completion_is_announced_only_by_the_sweep_that_flipped_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two sweeps can both select an invite while it is still 'consumed'. The
    second one's guarded UPDATE matches nothing — and announcing anyway was a
    duplicate in HR's bell."""
    import app.reminders as rem

    row = {
        "id": uuid.uuid4(), "company_id": uuid.uuid4(),
        "created_by_user_id": uuid.uuid4(),
        "full_name": "Priya", "job_title": "Backend Engineer",
    }
    db = _db()
    db.execute.return_value = MagicMock(
        first=MagicMock(return_value=None),  # another sweep got there first
        mappings=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[row]))),
    )

    sent = _capture_notifications(monkeypatch, rem)
    result = rem.SweepResult()
    await rem._interview_completed(db, result)

    assert sent == []
    assert result.completions == 0


def test_completion_is_found_by_the_scorecard_not_by_a_read() -> None:
    """The predicate is "a scorecard exists and the invite is still consumed" —
    state, not an event anyone has to observe. A sweep that misses a pass loses
    nothing, because the next one sees the same row."""
    from app.reminders import _COMPLETED_SQL

    assert "JOIN scorecards" in _COMPLETED_SQL
    assert "inv.status = 'consumed'" in _COMPLETED_SQL


def test_the_status_flip_is_its_own_idempotency_guard() -> None:
    """One-way transition on a single row: the row stops matching the moment it
    is announced. (The notification's dedupe key backs this up when two sweeps
    race — see test_completion_is_announced_only_by_the_sweep_that_flipped_it.)"""
    import inspect

    from app.reminders import _interview_completed

    src = inspect.getsource(_interview_completed)
    assert "status = 'completed'" in src
    assert "AND status = 'consumed'" in src  # the guard on the UPDATE itself


def test_the_invite_list_no_longer_writes() -> None:
    """A GET that mutates is a GET that behaves differently under a page
    refresh, a prefetch, or a second HR manager looking at the same list."""
    import inspect

    from app.routers.hr_interviews import list_invites

    src = inspect.getsource(list_invites)
    assert "db.commit()" not in src
    assert 'inv.status = "completed"' not in src


def test_the_candidate_is_told_in_app_as_well_as_by_email() -> None:
    """`a.user_id` was selected by the results query and never used, so a
    candidate whose mail bounced had no way to learn their scorecard existed."""
    # inspect.getsource on the live attribute — which only works because the
    # stage stubs in this module are installed through monkeypatch now and are
    # torn down after each test. They used to be raw assignments that were never
    # put back, so this read the stub instead of the function.
    import inspect

    from app.reminders import _results_ready

    src = inspect.getsource(_results_ready)
    assert "create_notification" in src
    assert '"/applications"' in src


def test_no_interview_completed_email_is_claimed() -> None:
    """There is no such template and there never has been — git history shows
    the claim arrived with the initial import. A comment asserting an email
    fires "exactly once" when none exists is worse than no comment."""
    from app.email_templates import _BUILDERS

    assert "interview_completed" not in _BUILDERS
    assert "results_ready" in _BUILDERS  # the one that does exist, for candidates
