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
async def test_one_bad_applicant_does_not_stop_the_pass() -> None:
    """The whole point of the loop is that it keeps going."""
    import app.reconciliation as rec

    good, bad = uuid.uuid4(), uuid.uuid4()
    db = _db()
    db.execute.return_value = MagicMock(all=MagicMock(return_value=[(bad,), (good,)]))

    applicant = MagicMock(
        id=good, company_id=uuid.uuid4(), resume_text="cv", target_job_title="Dev",
        target_level="mid", target_jd_text=None, created_by_user_id=uuid.uuid4(),
    )
    db.get = AsyncMock(return_value=applicant)

    calls: list[uuid.UUID] = []

    async def _score(**_: object) -> dict[str, int]:
        calls.append(bad if len(calls) == 0 else good)
        if len(calls) == 1:
            raise RuntimeError("scorer down")
        return {"overall": 7}

    rec.score_resume_remote = _score  # type: ignore[assignment]
    result = rec.PassResult()
    await rec._score_pass(db, result)

    assert result.failed == 1, "the failing row was not recorded"
    assert result.scored == 1, "the pass stopped instead of continuing"


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
async def test_exam_reminder_dedupe_key_names_the_window_not_the_time() -> None:
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

    rem.enqueue_email = _enqueue  # type: ignore[assignment]
    await rem._exam_reminders(db, rem.SweepResult())

    assert keys == [f"exam_reminder:{row['id']}:24h", f"exam_reminder:{row['id']}:1h"]


@pytest.mark.asyncio
async def test_a_failing_stage_does_not_sink_the_sweep() -> None:
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

    rem._exam_reminders = _boom  # type: ignore[assignment]
    rem._interview_reminders = _ok  # type: ignore[assignment]
    rem._expiry_notices = _ok  # type: ignore[assignment]
    rem._results_ready = _ok  # type: ignore[assignment]

    result = await rem.run_once(factory)  # type: ignore[arg-type]
    assert "exam" in order and order.count("results") == 3
    assert result.results_emails == 3


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
