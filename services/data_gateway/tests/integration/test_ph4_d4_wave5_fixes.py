"""PH4-D4 wave 5 — the security-review fixes, proven against a real,
migrated Postgres by calling the actual service functions (not mocked):

* H2 — consent moves to ``start``. Save, upload and submit are refused until
  ``consented_at`` is set; a second submission books its OWN ledger row; the
  sweep (``close_due``) never turns unconsented work into ``submitted``; and
  ``withdraw_task_consent`` blocks everything further and is a 409 on repeat.
* M1 — a reviewer and HR read submitted, consented work only.
* M5 — a task link dies with its application (a decided enrolment, a
  soft-deleted applicant), and a final decision withdraws what is still open.

Static/structural checks (the migration text, source wiring) live in
``tests/unit/test_ph4_d4_tasks.py`` — this file is the runtime behaviour.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio
from shared.db.engine import build_engine, build_session_factory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import job_tasks as svc
from app.config import settings
from app.interviewer_scorecards import RequestMeta

pytestmark = pytest.mark.integration

_META = RequestMeta(ip_address="127.0.0.1", user_agent="pytest")
_ITEM = '[{"key":"q1","prompt":"Explain your approach","response_type":"text","required":true}]'


class F:
    def __init__(self) -> None:
        self.company = uuid.uuid4()
        self.hr = uuid.uuid4()
        self.applicant = uuid.uuid4()
        self.req = uuid.uuid4()
        self.workflow = uuid.uuid4()
        self.round = uuid.uuid4()
        self.enrolment = uuid.uuid4()


async def _build(db: AsyncSession) -> F:
    f = F()
    tag = f.company.hex[:10]
    p: dict[str, Any] = {
        "c": f.company, "h": f.hr, "a": f.applicant, "r": f.req, "w": f.workflow,
        "rnd": f.round, "e": f.enrolment,
        "slug": f"w5-{tag}", "m1": f"h-{tag}@w5.test", "ae": f"asha-{tag}@w5.test",
        "it": _ITEM,
    }
    for sql in (
        "INSERT INTO companies (id, name, slug) VALUES (:c, 'W5 co', :slug)",
        "INSERT INTO users (id, email, company_id) VALUES (:h, :m1, :c)",
        "INSERT INTO job_requisitions (id, company_id, title, created_at, updated_at)"
        " VALUES (:r, :c, 'Engineer', now(), now())",
        "INSERT INTO applicants (id, company_id, full_name, email, target_job_title)"
        " VALUES (:a, :c, 'Asha', :ae, 'Engineer')",
        "INSERT INTO workflows (id, company_id, requisition_id, version, status, review_status,"
        " created_at, updated_at) VALUES (:w, :c, :r, 1, 'draft', 'draft', now(), now())",
        "INSERT INTO workflow_rounds (id, company_id, workflow_id, position, title, kind,"
        " deadline_days, created_at, updated_at)"
        " VALUES (:rnd, :c, :w, 0, 'Sim round', 'job_simulation', 7, now(), now())",
        "INSERT INTO enrolments (id, company_id, requisition_id, applicant_id, target_job_title,"
        " current_round_id, status, created_at, updated_at)"
        " VALUES (:e, :c, :r, :a, 'Engineer', :rnd, 'shortlisted', now(), now())",
        "INSERT INTO round_tasks (id, company_id, round_id, kind, brief, items,"
        " created_at, updated_at) VALUES (:rt, :c, :rnd, 'job_simulation', 'Do the thing',"
        " CAST(:it AS jsonb), now(), now())",
    ):
        await db.execute(text(sql), {**p, "rt": uuid.uuid4()})
    return f


async def _issue(
    db: AsyncSession, f: F, *, round_id: uuid.UUID | None = None,
    time_limit_seconds: int | None = None,
) -> tuple[uuid.UUID, str]:
    """A task_submissions row shaped the way ``job_tasks.issue`` would leave
    it, with a KNOWN raw token so a test can call the public service
    functions against it directly. ``due_at`` is always 7 days out — the
    lifecycle trigger refuses moving it EARLIER on an UPDATE (candidate
    fairness), so a test wanting an overdue submission uses ``_make_overdue``
    (below) rather than backdating ``due_at`` directly."""
    rid = round_id or f.round
    raw = svc.mint_task_token()
    sub_id = uuid.uuid4()
    cfg = await svc.get_config(db, company_id=f.company, round_id=rid)
    assert cfg is not None
    digest = svc.config_digest({k: v for k, v in cfg.items() if k != "round_id"})
    await db.execute(
        text(
            "INSERT INTO task_submissions (id, company_id, enrolment_id, round_id,"
            " applicant_id, kind, status, token_hash, due_at, time_limit_seconds,"
            " config_digest, created_at, updated_at)"
            " VALUES (:i,:c,:e,:r,:a,:k,'assigned',:th,"
            " now() + interval '7 days', :tl, :dig, now(), now())"
        ),
        {"i": sub_id, "c": f.company, "e": f.enrolment, "r": rid, "a": f.applicant,
         "k": cfg["kind"], "th": svc.hash_task_token(raw), "tl": time_limit_seconds, "dig": digest},
    )
    return sub_id, raw


_FILE_ITEM = (
    '[{"key":"q1","prompt":"Explain your approach","response_type":"text","required":true},'
    '{"key":"proof","prompt":"Attach evidence","response_type":"file","required":false}]'
)


async def _add_round_with_file_item(db: AsyncSession, f: F) -> uuid.UUID:
    """A job_simulation round with a FILE-type item — the shape gap 1 closes:
    before, no endpoint could ever store an answer for one."""
    rid = uuid.uuid4()
    await db.execute(
        text(
            "INSERT INTO workflow_rounds (id, company_id, workflow_id, position, title, kind,"
            " deadline_days, created_at, updated_at)"
            " VALUES (:r, :c, :w, 2, 'Sim round with file item', 'job_simulation', 7, now(), now())"
        ),
        {"r": rid, "c": f.company, "w": f.workflow},
    )
    await db.execute(
        text(
            "INSERT INTO round_tasks (id, company_id, round_id, kind, brief, items,"
            " created_at, updated_at) VALUES (:i, :c, :r, 'job_simulation', 'Do the thing',"
            " CAST(:it AS jsonb), now(), now())"
        ),
        {"i": uuid.uuid4(), "c": f.company, "r": rid, "it": _FILE_ITEM},
    )
    return rid


async def _add_portfolio_round(db: AsyncSession, f: F) -> uuid.UUID:
    rid = uuid.uuid4()
    await db.execute(
        text(
            "INSERT INTO workflow_rounds (id, company_id, workflow_id, position, title, kind,"
            " deadline_days, created_at, updated_at)"
            " VALUES (:r, :c, :w, 1, 'Portfolio round', 'portfolio', 7, now(), now())"
        ),
        {"r": rid, "c": f.company, "w": f.workflow},
    )
    await db.execute(
        text(
            "INSERT INTO round_tasks (id, company_id, round_id, kind, brief, items,"
            " min_artifacts, max_artifacts, allow_files, allow_links, allowed_link_domains,"
            " created_at, updated_at)"
            " VALUES (:i, :c, :r, 'portfolio', 'Share your work', '[]'::jsonb,"
            " 0, 5, true, true, ARRAY['github.com'], now(), now())"
        ),
        {"i": uuid.uuid4(), "c": f.company, "r": rid},
    )
    return rid


@pytest_asyncio.fixture
async def db() -> AsyncIterator[AsyncSession]:
    """One transaction per test, always rolled back — nothing is left behind."""
    engine = build_engine(
        database_url=settings.database_url, database_ssl=settings.database_ssl, pool_size=2,
    )
    factory = build_session_factory(engine)
    try:
        async with factory() as session:
            await session.begin()
            try:
                yield session
            finally:
                await session.rollback()
    finally:
        await engine.dispose()


# ===========================================================================
# H2 — consent moves to `start`
# ===========================================================================
@pytest.mark.asyncio
async def test_start_without_consent_is_refused(db: AsyncSession) -> None:
    f = await _build(db)
    _sub_id, raw = await _issue(db, f)
    with pytest.raises(svc.TaskError) as exc:
        await svc.start(db, raw=raw, consent=False, meta=_META)
    assert exc.value.status_code == 422
    status_ = await db.scalar(text("SELECT status FROM task_submissions WHERE id = :i"), {"i": _sub_id})
    assert status_ == "assigned"


@pytest.mark.asyncio
async def test_save_response_before_start_is_refused(db: AsyncSession) -> None:
    f = await _build(db)
    _sub_id, raw = await _issue(db, f)
    with pytest.raises(svc.TaskError) as exc:
        await svc.save_response(
            db, raw=raw, item_key="q1", text_value="too early", link_url=None, meta=_META,
        )
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_add_artifact_before_start_is_refused(db: AsyncSession) -> None:
    f = await _build(db)
    # A portfolio kind isn't required for this: add_artifact's consent gate
    # runs before the kind check.
    _sub_id, raw = await _issue(db, f)
    with pytest.raises(svc.TaskError) as exc:
        await svc.add_artifact(
            db, raw=raw, kind="link", data=None, filename=None,
            link_url="https://github.com/x/y", link_kind="repository",
            title=None, description=None, meta=_META,
        )
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_submit_before_start_is_refused(db: AsyncSession) -> None:
    f = await _build(db)
    _sub_id, raw = await _issue(db, f)
    with pytest.raises(svc.TaskError) as exc:
        await svc.submit(db, raw=raw, consent=True, meta=_META)
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_start_with_consent_books_exactly_one_ledger_row(db: AsyncSession) -> None:
    f = await _build(db)
    sub_id, raw = await _issue(db, f)
    out = await svc.start(db, raw=raw, consent=True, meta=_META)
    assert out["status"] == "in_progress"
    consented_at = await db.scalar(
        text("SELECT consented_at FROM task_submissions WHERE id = :i"), {"i": sub_id},
    )
    assert consented_at is not None
    rows = (
        await db.execute(
            text(
                "SELECT evidence ->> 'submission_id' AS sid FROM dpdp_consent_ledger"
                " WHERE consent_type = 'assessment_submission' AND purpose = 'recruitment'"
            )
        )
    ).scalars().all()
    assert rows.count(str(sub_id)) == 1


@pytest.mark.asyncio
async def test_a_second_submission_gets_its_own_ledger_row(db: AsyncSession) -> None:
    """H2(d): the dedup used to be per USER, so a second submission got no
    ledger row of its own."""
    f = await _build(db)
    sub1, raw1 = await _issue(db, f)
    await svc.start(db, raw=raw1, consent=True, meta=_META)

    round2 = uuid.uuid4()
    await db.execute(
        text(
            "INSERT INTO workflow_rounds (id, company_id, workflow_id, position, title, kind,"
            " deadline_days, created_at, updated_at)"
            " VALUES (:r, :c, :w, 1, 'Sim round 2', 'job_simulation', 7, now(), now())"
        ),
        {"r": round2, "c": f.company, "w": f.workflow},
    )
    await db.execute(
        text(
            "INSERT INTO round_tasks (id, company_id, round_id, kind, brief, items,"
            " created_at, updated_at) VALUES (:i, :c, :r, 'job_simulation', 'Do another thing',"
            " CAST(:it AS jsonb), now(), now())"
        ),
        {"i": uuid.uuid4(), "c": f.company, "r": round2, "it": _ITEM},
    )
    sub2, raw2 = await _issue(db, f, round_id=round2)
    await svc.start(db, raw=raw2, consent=True, meta=_META)

    # provision_guest_user links ONE guest identity to the applicant, reused
    # by the second start() — scope to it, since dpdp_consent_ledger has no
    # tenant column and this DB is shared with every other test/smoke run.
    user_id = await db.scalar(
        text("SELECT user_id FROM applicants WHERE id = :a"), {"a": f.applicant},
    )
    rows = (
        await db.execute(
            text(
                "SELECT evidence ->> 'submission_id' AS sid FROM dpdp_consent_ledger"
                " WHERE consent_type = 'assessment_submission' AND purpose = 'recruitment'"
                "   AND user_id = :u"
            ),
            {"u": user_id},
        )
    ).scalars().all()
    assert str(sub1) in rows
    assert str(sub2) in rows
    assert len(rows) == 2


async def _make_overdue(db: AsyncSession, sub_id: uuid.UUID) -> None:
    """Push a submission past its (short) time limit without ever moving
    ``due_at`` backward — the lifecycle trigger refuses that on an UPDATE
    (candidate fairness: a deadline can be extended, never shortened
    retroactively). ``started_at`` carries no such freeze, so backdating it
    is how a test fast-forwards the clock instead."""
    await db.execute(
        text("UPDATE task_submissions SET started_at = now() - interval '1 hour' WHERE id = :i"),
        {"i": sub_id},
    )


@pytest.mark.asyncio
async def test_close_due_expires_rather_than_submits_unconsented_work(db: AsyncSession) -> None:
    """H2(a): the sweep must never turn unconsented work into a submission.
    A withdrawal (``withdraw_task_consent``) is the only way an in_progress
    row can lack consent today; this reproduces exactly that state."""
    f = await _build(db)
    sub_id, raw = await _issue(db, f, time_limit_seconds=60)
    await svc.start(db, raw=raw, consent=True, meta=_META)
    await svc.save_response(
        db, raw=raw, item_key="q1", text_value="an answer", link_url=None, meta=_META,
    )
    await svc.withdraw_task_consent(db, raw=raw, meta=_META)
    await _make_overdue(db, sub_id)
    closed = await svc.close_due(db)
    assert closed == 1
    status_ = await db.scalar(text("SELECT status FROM task_submissions WHERE id = :i"), {"i": sub_id})
    assert status_ == "expired"


@pytest.mark.asyncio
async def test_close_due_submits_consented_work(db: AsyncSession) -> None:
    f = await _build(db)
    sub_id, raw = await _issue(db, f, time_limit_seconds=60)
    await svc.start(db, raw=raw, consent=True, meta=_META)
    await svc.save_response(
        db, raw=raw, item_key="q1", text_value="an answer", link_url=None, meta=_META,
    )
    await _make_overdue(db, sub_id)
    closed = await svc.close_due(db)
    assert closed == 1
    status_ = await db.scalar(text("SELECT status FROM task_submissions WHERE id = :i"), {"i": sub_id})
    assert status_ == "submitted"


@pytest.mark.asyncio
async def test_withdraw_consent_blocks_further_saves_and_is_409_on_repeat(db: AsyncSession) -> None:
    f = await _build(db)
    _sub_id, raw = await _issue(db, f)
    await svc.start(db, raw=raw, consent=True, meta=_META)
    out = await svc.withdraw_task_consent(db, raw=raw, meta=_META)
    assert out == {"withdrawn": True}

    with pytest.raises(svc.TaskError) as exc:
        await svc.save_response(
            db, raw=raw, item_key="q1", text_value="too late", link_url=None, meta=_META,
        )
    assert exc.value.status_code == 409

    with pytest.raises(svc.TaskError) as exc2:
        await svc.withdraw_task_consent(db, raw=raw, meta=_META)
    assert exc2.value.status_code == 409


# ===========================================================================
# M6 — retention is decision-aware: a HELD application never loses its
# submitted evidence on its own clock; a DECIDED one loses it after the
# decision, not after the submission.
# ===========================================================================
@pytest.mark.asyncio
async def test_retention_never_purges_a_held_applications_evidence(db: AsyncSession) -> None:
    """A submission's own timestamp is irrelevant once its status is
    'submitted' -- the OLD, submission-only clock
    (``COALESCE(submitted_at, updated_at)``) would have purged this the
    moment ``retention_days`` elapsed, decided or not. ``retention_days=0``
    makes that cutoff "right now", so this proves the decision gate alone —
    not merely that a fresh submission is not yet old enough."""
    f = await _build(db)
    sub_id, raw = await _issue(db, f)
    await svc.start(db, raw=raw, consent=True, meta=_META)
    await svc.save_response(
        db, raw=raw, item_key="q1", text_value="an answer", link_url=None, meta=_META,
    )
    await svc.submit(db, raw=raw, consent=True, meta=_META)
    await db.execute(text("UPDATE enrolments SET status = 'held' WHERE id = :e"), {"e": f.enrolment})
    purged = await svc.purge(db, retention_days=0, dry_run=False)
    assert purged == 0
    redacted = await db.scalar(
        text("SELECT redacted_at FROM task_submissions WHERE id = :i"), {"i": sub_id},
    )
    assert redacted is None


@pytest.mark.asyncio
async def test_retention_purges_submitted_evidence_after_the_application_is_decided(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # purge() always probes object storage for orphaned keys under the
    # submission's prefix, even when no file response exists — fake it out
    # rather than hitting real S3/R2 with the test's placeholder credentials.
    async def _no_keys(*_a: object, **_kw: object) -> list[str]:
        return []

    async def _no_remove(*_a: object, **_kw: object) -> int:
        return 0

    monkeypatch.setattr(svc.store, "keys_under", _no_keys)
    monkeypatch.setattr(svc.store, "remove", _no_remove)

    f = await _build(db)
    sub_id, raw = await _issue(db, f)
    await svc.start(db, raw=raw, consent=True, meta=_META)
    await svc.save_response(
        db, raw=raw, item_key="q1", text_value="an answer", link_url=None, meta=_META,
    )
    await svc.submit(db, raw=raw, consent=True, meta=_META)  # submitted_at is recent
    await db.execute(text("UPDATE enrolments SET status = 'rejected' WHERE id = :e"), {"e": f.enrolment})
    await db.execute(
        text(
            "INSERT INTO stage_transitions (company_id, enrolment_id, from_status, to_status,"
            " actor_user_id, automated, reason, reason_code, reason_label, occurred_at)"
            " VALUES (:c, :e, 'shortlisted', 'rejected', :u, false, 'not a fit',"
            " 'role_fit', 'Role fit', now() - interval '200 days')"
        ),
        {"c": f.company, "e": f.enrolment, "u": f.hr},
    )
    purged = await svc.purge(db, retention_days=180, dry_run=False)
    assert purged == 1
    redacted = await db.scalar(
        text("SELECT redacted_at FROM task_submissions WHERE id = :i"), {"i": sub_id},
    )
    assert redacted is not None


# ===========================================================================
# M1 — a reviewer / HR reads submitted, consented work only
# ===========================================================================
@pytest.mark.asyncio
async def test_reviewer_cannot_read_an_unsubmitted_draft(db: AsyncSession) -> None:
    f = await _build(db)
    iv = uuid.uuid4()
    await db.execute(
        text("INSERT INTO users (id, email, company_id) VALUES (:i, :e, :c)"),
        {"i": iv, "e": f"iv-{f.company.hex[:8]}@w5.test", "c": f.company},
    )
    sub_id, raw = await _issue(db, f)
    await svc.start(db, raw=raw, consent=True, meta=_META)
    await svc.save_response(
        db, raw=raw, item_key="q1", text_value="a draft answer", link_url=None, meta=_META,
    )
    scorecard_id = uuid.uuid4()
    await db.execute(
        text(
            "INSERT INTO interviewer_scorecards (id, company_id, enrolment_id, round_id,"
            " interviewer_user_id, status) VALUES (:i, :c, :e, :r, :iv, 'in_progress')"
        ),
        {"i": scorecard_id, "c": f.company, "e": f.enrolment, "r": f.round, "iv": iv},
    )

    with pytest.raises(svc.TaskError) as exc:
        await svc.submission_for_reviewer(
            db, company_id=f.company, scorecard_id=scorecard_id, interviewer_user_id=iv,
        )
    assert exc.value.status_code == 404

    await svc.submit(db, raw=raw, consent=True, meta=_META)
    out = await svc.submission_for_reviewer(
        db, company_id=f.company, scorecard_id=scorecard_id, interviewer_user_id=iv,
    )
    assert out["status"] == "submitted"
    assert out["submission_id"] == str(sub_id)
    # Gap 2: the round's own brief and item prompts, not just keys/answers.
    assert out["brief"] == "Do the thing"
    assert out["items"][0]["key"] == "q1"
    assert any(r["text_value"] == "a draft answer" for r in out["responses"])


@pytest.mark.asyncio
async def test_hr_artifact_download_refuses_an_unsubmitted_draft(db: AsyncSession) -> None:
    f = await _build(db)
    portfolio_round = await _add_portfolio_round(db, f)
    sub_id, raw = await _issue(db, f, round_id=portfolio_round)
    await svc.start(db, raw=raw, consent=True, meta=_META)
    resp = await svc.add_artifact(
        db, raw=raw, kind="link", data=None, filename=None,
        link_url="https://github.com/asha/demo", link_kind="repository",
        title="demo", description=None, meta=_META,
    )
    response_id = uuid.UUID(resp["id"])
    with pytest.raises(svc.TaskError) as exc:
        await svc.artifact_download(
            db, company_id=f.company, submission_id=sub_id, response_id=response_id,
            actor=f.hr, actor_type="user", meta=_META,
        )
    assert exc.value.status_code == 404


# ===========================================================================
# M5 — a task link dies with its application
# ===========================================================================
@pytest.mark.asyncio
async def test_link_dies_once_the_application_is_decided(db: AsyncSession) -> None:
    f = await _build(db)
    _sub_id, raw = await _issue(db, f)
    await db.execute(text("UPDATE enrolments SET status = 'rejected' WHERE id = :e"), {"e": f.enrolment})
    with pytest.raises(svc.TaskError) as exc:
        await svc.by_token(db, raw)
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_link_dies_once_the_applicant_is_erased(db: AsyncSession) -> None:
    f = await _build(db)
    _sub_id, raw = await _issue(db, f)
    await db.execute(text("UPDATE applicants SET deleted_at = now() WHERE id = :a"), {"a": f.applicant})
    with pytest.raises(svc.TaskError) as exc:
        await svc.by_token(db, raw)
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_close_for_decision_withdraws_open_task_submissions(db: AsyncSession) -> None:
    f = await _build(db)
    sub_id, raw = await _issue(db, f)
    await svc.start(db, raw=raw, consent=True, meta=_META)
    closed = await svc.close_for_decision(
        db, company_id=f.company, enrolment_id=f.enrolment, actor=f.hr, meta=_META,
    )
    assert closed == 1
    status_, token_hash = (
        await db.execute(
            text("SELECT status, token_hash FROM task_submissions WHERE id = :i"), {"i": sub_id},
        )
    ).first()
    assert status_ == "withdrawn"
    assert token_hash is None


@pytest.mark.asyncio
async def test_close_for_decision_leaves_a_submitted_row_alone(db: AsyncSession) -> None:
    f = await _build(db)
    sub_id, raw = await _issue(db, f)
    await svc.start(db, raw=raw, consent=True, meta=_META)
    await svc.save_response(
        db, raw=raw, item_key="q1", text_value="an answer", link_url=None, meta=_META,
    )
    await svc.submit(db, raw=raw, consent=True, meta=_META)
    closed = await svc.close_for_decision(
        db, company_id=f.company, enrolment_id=f.enrolment, actor=f.hr, meta=_META,
    )
    assert closed == 0
    status_ = await db.scalar(text("SELECT status FROM task_submissions WHERE id = :i"), {"i": sub_id})
    assert status_ == "submitted"


# ===========================================================================
# M4 — a timed task withholds items and materials until started
# ===========================================================================
@pytest.mark.asyncio
async def test_timed_task_withholds_items_until_started(db: AsyncSession) -> None:
    f = await _build(db)
    _sub_id, raw = await _issue(db, f, time_limit_seconds=1800)
    sub = await svc.by_token(db, raw)
    pre = await svc.candidate_view(db, sub)
    assert pre["items"] == []

    await svc.start(db, raw=raw, consent=True, meta=_META)
    sub2 = await svc.by_token(db, raw)
    post = await svc.candidate_view(db, sub2)
    assert post["items"] != []


# ===========================================================================
# Once submitted, the link stops serving content (security review wave 5,
# item 2) — the fragment can outlive the browser tab on a shared machine.
# ===========================================================================
@pytest.mark.asyncio
async def test_submitted_view_carries_no_content(db: AsyncSession) -> None:
    f = await _build(db)
    _sub_id, raw = await _issue(db, f)
    await svc.start(db, raw=raw, consent=True, meta=_META)
    await svc.save_response(
        db, raw=raw, item_key="q1", text_value="an answer", link_url=None, meta=_META,
    )
    await svc.submit(db, raw=raw, consent=True, meta=_META)
    sub = await svc.by_token(db, raw)
    out = await svc.candidate_view(db, sub)
    assert out["status"] == "submitted"
    assert out["items"] == []
    assert out["responses"] == []
    assert out["brief"] == ""


# ===========================================================================
# L3 — reissuing a LIVE submission must actually replace the link
# ===========================================================================
@pytest.mark.asyncio
async def test_reissuing_a_live_submission_mints_a_new_token(db: AsyncSession) -> None:
    """Before the fix, ``issue``'s own "already live, reuse it" shortcut
    (meant for the workflow runner) made a manual reissue of a still-open
    submission a no-op that still logged as a replacement."""
    f = await _build(db)
    sub_id, raw = await _issue(db, f)  # still 'assigned' — never started

    out = await svc.reissue(db, company_id=f.company, submission_id=sub_id, actor=f.hr, meta=_META)
    new_id = uuid.UUID(out["id"])
    assert new_id != sub_id

    old_status, old_token = (
        await db.execute(
            text("SELECT status, token_hash FROM task_submissions WHERE id = :i"), {"i": sub_id},
        )
    ).first()
    assert old_status == "withdrawn"
    assert old_token is None

    new_token_hash = await db.scalar(
        text("SELECT token_hash FROM task_submissions WHERE id = :i"), {"i": new_id},
    )
    assert new_token_hash is not None
    assert new_token_hash != svc.hash_task_token(raw)

    # The old link is truly dead now, not merely relabelled.
    with pytest.raises(svc.TaskError):
        await svc.by_token(db, raw)


# ===========================================================================
# Gap 1 — a file answer on a specific ITEM (never reachable before)
# ===========================================================================
class _FakeStore:
    """In-memory stand-in for object storage — no network, no real
    credentials needed (the smoke_ph4_d4_tasks.py precedent)."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    async def store(self, _settings: object, key: str, data: bytes, _content_type: str) -> None:
        self.objects[key] = data

    async def remove(self, _settings: object, keys: list[str]) -> int:
        removed = 0
        for k in keys:
            if self.objects.pop(k, None) is not None:
                removed += 1
        return removed


@pytest.mark.asyncio
async def test_a_file_item_can_be_answered_and_replaced(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_store = _FakeStore()
    monkeypatch.setattr(svc.store, "store", fake_store.store)
    monkeypatch.setattr(svc.store, "remove", fake_store.remove)

    f = await _build(db)
    rid = await _add_round_with_file_item(db, f)
    sub_id, raw = await _issue(db, f, round_id=rid)
    await svc.start(db, raw=raw, consent=True, meta=_META)

    first = await svc.add_artifact(
        db, raw=raw, kind="file", data=b"%PDF-1.4", filename="proof.pdf",
        link_url=None, link_kind=None, title=None, description=None, meta=_META,
        item_key="proof",
    )
    row = (
        await db.execute(
            text("SELECT item_key, response_type FROM task_responses WHERE id = :i"),
            {"i": uuid.UUID(first["id"])},
        )
    ).mappings().first()
    assert row is not None
    assert row["item_key"] == "proof"
    assert row["response_type"] == "file"

    # It counts toward the ITEM, never the free-form artifact limits (this
    # round has none configured at all — min/max_artifacts are NULL).
    responses = await svc._responses_for(db, sub_id)
    assert len(responses) == 1

    # Replaceable: a second upload for the same item removes the first row
    # (a file's storage_key never changes in place) rather than erroring.
    second = await svc.add_artifact(
        db, raw=raw, kind="file", data=b"%PDF-1.4", filename="proof-v2.pdf",
        link_url=None, link_kind=None, title=None, description=None, meta=_META,
        item_key="proof",
    )
    assert second["id"] != first["id"]
    still_there = await db.scalar(
        text("SELECT 1 FROM task_responses WHERE id = :i"), {"i": uuid.UUID(first["id"])},
    )
    assert still_there is None
    responses_after = await svc._responses_for(db, sub_id)
    assert len(responses_after) == 1

    # M2 — the candidate can retract it outright, too.
    key = await svc.remove_artifact(db, raw=raw, response_id=uuid.UUID(second["id"]), meta=_META)
    assert key is not None
    empty = await svc._responses_for(db, sub_id)
    assert empty == []


# ===========================================================================
# Security re-review of 69435c0 (NEW-1 .. NEW-6)
# ===========================================================================
async def _active_ledger_rows(db: AsyncSession, sub_id: uuid.UUID) -> int:
    return int(
        await db.scalar(
            text(
                "SELECT count(*) FROM dpdp_consent_ledger"
                " WHERE consent_type = 'assessment_submission' AND purpose = 'recruitment'"
                "   AND evidence ->> 'submission_id' = :s AND granted AND revoked_at IS NULL"
            ),
            {"s": str(sub_id)},
        )
        or 0
    )


@pytest.mark.asyncio
async def test_a_withdrawal_on_one_submission_does_not_strand_the_next(db: AsyncSession) -> None:
    """NEW-1: the ledger write refused if the candidate had EVER withdrawn on
    any submission, after `start` had already set `consented_at` -- so the
    next submission ran on a consent with no ledger row, and withdrawing it
    was a 409 "already withdrawn" while processing carried on."""
    f = await _build(db)
    sub_a, raw_a = await _issue(db, f)
    await svc.start(db, raw=raw_a, consent=True, meta=_META)
    await svc.withdraw_task_consent(db, raw=raw_a, meta=_META)

    round_b = await _add_round_with_file_item(db, f)
    sub_b, raw_b = await _issue(db, f, round_id=round_b)
    await svc.start(db, raw=raw_b, consent=True, meta=_META)

    assert await _active_ledger_rows(db, sub_a) == 0
    assert await _active_ledger_rows(db, sub_b) == 1
    assert await svc.withdraw_task_consent(db, raw=raw_b, meta=_META) == {"withdrawn": True}
    assert await _active_ledger_rows(db, sub_b) == 0
    assert await db.scalar(
        text("SELECT consented_at FROM task_submissions WHERE id = :i"), {"i": sub_b},
    ) is None


@pytest.mark.asyncio
async def test_consent_can_be_withdrawn_after_submitting_and_hides_the_work(
    db: AsyncSession,
) -> None:
    """NEW-2: the link refused once submitted ("no consent left to
    withdraw") while the ledger row stayed active and reviewers kept reading."""
    f = await _build(db)
    iv = uuid.uuid4()
    await db.execute(
        text("INSERT INTO users (id, email, company_id) VALUES (:i, :e, :c)"),
        {"i": iv, "e": f"iv2-{f.company.hex[:8]}@w5.test", "c": f.company},
    )
    sub_id, raw = await _issue(db, f)
    await svc.start(db, raw=raw, consent=True, meta=_META)
    await svc.save_response(
        db, raw=raw, item_key="q1", text_value="my answer", link_url=None, meta=_META,
    )
    await svc.submit(db, raw=raw, consent=True, meta=_META)
    scorecard_id = uuid.uuid4()
    await db.execute(
        text(
            "INSERT INTO interviewer_scorecards (id, company_id, enrolment_id, round_id,"
            " interviewer_user_id, status) VALUES (:i, :c, :e, :r, :iv, 'in_progress')"
        ),
        {"i": scorecard_id, "c": f.company, "e": f.enrolment, "r": f.round, "iv": iv},
    )
    before = await svc.submission_for_reviewer(
        db, company_id=f.company, scorecard_id=scorecard_id, interviewer_user_id=iv,
    )
    assert before["responses"]

    assert await svc.withdraw_task_consent(db, raw=raw, meta=_META) == {"withdrawn": True}
    assert await _active_ledger_rows(db, sub_id) == 0

    # The reviewer no longer sees it, HR sees it as withdrawn (not as an
    # empty submission), and a reload of the candidate's own page says so.
    with pytest.raises(svc.TaskError) as exc:
        await svc.submission_for_reviewer(
            db, company_id=f.company, scorecard_id=scorecard_id, interviewer_user_id=iv,
        )
    assert exc.value.status_code == 404
    [entry] = await svc.for_enrolment(
        db, company_id=f.company, enrolment_id=f.enrolment, actor=f.hr,
    )
    assert entry["status"] == "submitted"
    assert entry["consent_withdrawn"] is True
    assert entry["responses"] == []
    view = await svc.candidate_view(db, await svc.by_token(db, raw))
    assert view["consent_withdrawn"] is True

    with pytest.raises(svc.TaskError) as again:
        await svc.withdraw_task_consent(db, raw=raw, meta=_META)
    assert again.value.status_code == 409


@pytest.mark.asyncio
async def test_the_window_closed_notice_does_not_say_withdrawn_work_was_sent(
    db: AsyncSession,
) -> None:
    """NEW-3: `has_work` looked only at saved answers, so a candidate who
    withdrew consent was told their work "was sent to the hiring team" while
    the sweep expired it. It now matches close_due: saved work AND consent."""
    from datetime import UTC, datetime, timedelta

    from app import reminders

    f = await _build(db)
    withdrawn_id, raw_w = await _issue(db, f)
    await svc.start(db, raw=raw_w, consent=True, meta=_META)
    await svc.save_response(
        db, raw=raw_w, item_key="q1", text_value="an answer", link_url=None, meta=_META,
    )
    await svc.withdraw_task_consent(db, raw=raw_w, meta=_META)

    kept_round = await _add_round_with_file_item(db, f)
    kept_id, raw_k = await _issue(db, f, round_id=kept_round)
    await svc.start(db, raw=raw_k, consent=True, meta=_META)
    await svc.save_response(
        db, raw=raw_k, item_key="q1", text_value="an answer", link_url=None, meta=_META,
    )

    # Read the lapsed stage as of a moment after both due dates, rather than
    # moving due_at backwards (the lifecycle trigger refuses that).
    now = datetime.now(tz=UTC)
    rows = (
        await db.execute(
            text(reminders._LAPSED_SQL),
            {"now": now + timedelta(days=8), "floor": now, "lim": 10000},
        )
    ).mappings().all()
    has_work = {r["id"]: r["has_work"] for r in rows if r["kind"] == "task"}
    assert has_work[withdrawn_id] is False
    assert has_work[kept_id] is True


@pytest.mark.asyncio
async def test_a_lost_guest_identity_race_raises_instead_of_overwriting(
    db: AsyncSession,
) -> None:
    """NEW-5: `uq_applicants_user_id` is on user_id, so a second, concurrent
    link never collided with the first -- its UPDATE waited, then overwrote
    applicants.user_id, orphaning the first identity and its consent row.
    Run sequentially, this is exactly what the losing request's UPDATE sees."""
    from datetime import UTC, datetime

    from sqlalchemy.exc import IntegrityError

    from app.guest_identity import GuestIdentityRaceError, provision_guest_user

    f = await _build(db)
    now = datetime.now(tz=UTC)
    winner = await provision_guest_user(
        db, applicant_id=f.applicant, full_name="Asha", company_id=f.company,
        language="en", email_prefix="task", now=now,
    )
    # The callers recover on IntegrityError; the race must arrive as one.
    with pytest.raises(IntegrityError) as exc:
        await provision_guest_user(
            db, applicant_id=f.applicant, full_name="Asha", company_id=f.company,
            language="en", email_prefix="invite", now=now,
        )
    assert isinstance(exc.value, GuestIdentityRaceError)
    assert await db.scalar(
        text("SELECT user_id FROM applicants WHERE id = :a"), {"a": f.applicant},
    ) == winner


@pytest.mark.asyncio
async def test_replacing_a_file_answer_leaves_the_old_object_until_commit(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """NEW-6: the old object was deleted inside the service, before the
    caller's commit -- a failed commit restored the old row pointing at a
    file that no longer existed. The service now hands the old key back for
    the router to remove after a successful commit."""
    fake_store = _FakeStore()
    monkeypatch.setattr(svc.store, "store", fake_store.store)
    monkeypatch.setattr(svc.store, "remove", fake_store.remove)

    f = await _build(db)
    rid = await _add_round_with_file_item(db, f)
    _sub_id, raw = await _issue(db, f, round_id=rid)
    await svc.start(db, raw=raw, consent=True, meta=_META)
    first = await svc.add_artifact(
        db, raw=raw, kind="file", data=b"%PDF-1.4", filename="a.pdf",
        link_url=None, link_kind=None, title=None, description=None, meta=_META,
        item_key="proof",
    )
    assert first["_replaced_key"] is None
    second = await svc.add_artifact(
        db, raw=raw, kind="file", data=b"%PDF-1.4", filename="b.pdf",
        link_url=None, link_kind=None, title=None, description=None, meta=_META,
        item_key="proof",
    )
    assert second["_replaced_key"] == first["_storage_key"]
    assert first["_storage_key"] in fake_store.objects  # not removed before commit
    assert second["_storage_key"] in fake_store.objects


@pytest.mark.asyncio
async def test_the_database_allows_consent_only_at_start_and_then_only_withdrawal(
    db: AsyncSession,
) -> None:
    """The trigger backstop behind NEW-1/NEW-2: whatever the application code
    does, `consented_at` is set only by the start transition and afterwards
    only cleared -- so a withdrawal can never be quietly re-granted."""
    from sqlalchemy.exc import DBAPIError

    f = await _build(db)
    sub_id, raw = await _issue(db, f)
    await svc.start(db, raw=raw, consent=True, meta=_META)
    await svc.save_response(
        db, raw=raw, item_key="q1", text_value="an answer", link_url=None, meta=_META,
    )
    await svc.withdraw_task_consent(db, raw=raw, meta=_META)

    with pytest.raises(DBAPIError, match="consent is given only at start"):
        async with db.begin_nested():
            await db.execute(
                text("UPDATE task_submissions SET consented_at = now() WHERE id = :i"),
                {"i": sub_id},
            )

    with pytest.raises(DBAPIError, match="arrives without consent"):
        async with db.begin_nested():
            await db.execute(
                text(
                    "INSERT INTO task_submissions (id, company_id, enrolment_id, round_id,"
                    " applicant_id, kind, status, token_hash, due_at, config_digest,"
                    " consented_at, created_at, updated_at)"
                    " VALUES (:i,:c,:e,:r,:a,'job_simulation','assigned',:th,"
                    " now() + interval '7 days', 'x', now(), now(), now())"
                ),
                {"i": uuid.uuid4(), "c": f.company, "e": f.enrolment, "r": f.round,
                 "a": f.applicant, "th": svc.hash_task_token(svc.mint_task_token())},
            )


@pytest.mark.asyncio
async def test_hr_reads_submitted_work_with_its_prompts_and_the_read_is_recorded(
    db: AsyncSession,
) -> None:
    """Gap 4, finished: HR's list carries the answers AND the brief and item
    prompts they answer (the reviewer read has had these since gap 2), and
    each read of a candidate's work is recorded against the HR user, as a
    reviewer's read is."""
    f = await _build(db)
    sub_id, raw = await _issue(db, f)
    await svc.start(db, raw=raw, consent=True, meta=_META)
    await svc.save_response(
        db, raw=raw, item_key="q1", text_value="my answer", link_url=None, meta=_META,
    )

    [draft] = await svc.for_enrolment(
        db, company_id=f.company, enrolment_id=f.enrolment, actor=f.hr,
    )
    assert draft["responses"] == [] and draft["items"] == [] and draft["brief"] is None

    await svc.submit(db, raw=raw, consent=True, meta=_META)
    [entry] = await svc.for_enrolment(
        db, company_id=f.company, enrolment_id=f.enrolment, actor=f.hr,
    )
    assert entry["brief"] == "Do the thing"
    assert entry["items"][0]["prompt"] == "Explain your approach"
    assert entry["responses"][0]["text_value"] == "my answer"
    viewed = await db.scalar(
        text(
            "SELECT count(*) FROM task_events WHERE submission_id = :s"
            " AND action = 'submission_viewed' AND actor_user_id = :u"
        ),
        {"s": sub_id, "u": f.hr},
    )
    assert viewed == 1  # the draft read above recorded nothing


@pytest.mark.asyncio
async def test_one_candidates_token_cannot_reach_another_candidates_work(
    db: AsyncSession,
) -> None:
    """Criterion 27 (the evidence pass found no test of it): every public
    task call resolves the submission from the caller's OWN token, so a
    candidate holding a valid link cannot read or remove a second
    candidate's answers by guessing or learning their ids."""
    f = await _build(db)
    portfolio_round = await _add_portfolio_round(db, f)

    # A second candidate in the same company and round.
    other_applicant, other_enrolment = uuid.uuid4(), uuid.uuid4()
    await db.execute(
        text("INSERT INTO applicants (id, company_id, full_name, email, target_job_title)"
             " VALUES (:a, :c, 'Bala', :e, 'Engineer')"),
        {"a": other_applicant, "c": f.company, "e": f"bala-{f.company.hex[:8]}@w5.test"},
    )
    await db.execute(
        text("INSERT INTO enrolments (id, company_id, requisition_id, applicant_id,"
             " target_job_title, current_round_id, status, created_at, updated_at)"
             " VALUES (:e, :c, :r, :a, 'Engineer', :rnd, 'shortlisted', now(), now())"),
        {"e": other_enrolment, "c": f.company, "r": f.req, "a": other_applicant,
         "rnd": portfolio_round},
    )
    _sub_a, raw_a = await _issue(db, f, round_id=portfolio_round)
    other = F()
    other.company, other.applicant, other.enrolment = f.company, other_applicant, other_enrolment
    _sub_b, raw_b = await _issue(db, other, round_id=portfolio_round)

    await svc.start(db, raw=raw_a, consent=True, meta=_META)
    await svc.start(db, raw=raw_b, consent=True, meta=_META)
    b_artifact = await svc.add_artifact(
        db, raw=raw_b, kind="link", data=None, filename=None,
        link_url="https://github.com/bala/private-work", link_kind="repository",
        title="Bala's work", description=None, meta=_META,
    )

    view_a = await svc.candidate_view(db, await svc.by_token(db, raw_a))
    assert view_a["responses"] == []
    assert "bala" not in str(view_a).lower()

    with pytest.raises(svc.TaskError) as exc:
        await svc.remove_artifact(
            db, raw=raw_a, response_id=uuid.UUID(b_artifact["id"]), meta=_META,
        )
    assert exc.value.status_code == 404
    still = await db.scalar(
        text("SELECT 1 FROM task_responses WHERE id = :i"), {"i": uuid.UUID(b_artifact["id"])},
    )
    assert still == 1
