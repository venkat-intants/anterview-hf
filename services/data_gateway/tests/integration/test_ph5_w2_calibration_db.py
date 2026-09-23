"""PH5 Wave 2 / E4 — interviewer calibration & outcome learning, against real Postgres.

Pure-arithmetic and registry coverage lives in
``tests/unit/test_ph5_w2_calibration.py``. This file is everything that needs
a database: two rounds sharing a competency id staying separate at the
company's real data, the archived-workflow freeze calibration's frozen-criterion
keying depends on, the independence rule in the judgements drill-down, tenant
isolation, outcome linkage (bands, a reversed hire, an AI-only hire, a
corrected scorecard, a deleted check-in, suppressed cells), cohort windows,
and the "All" hires consistency between ``/hr/analytics/outcome-signals`` and
``/hr/analytics/funnel``.

NO NEW MIGRATION. The design's §2.3 recommendation (a migration widening
``workflow_children_immutable()``/``workflows_published_immutable()`` from
``published`` to ``published`` OR ``archived``) was written against migration
``f3b5d7a9c1e4`` alone. Migration ``a2b4c6d8e0f1`` (PH4-O6, "workflow versions
are reviewed and approved") already superseded both functions before this
wave started: ``workflow_children_immutable()`` already refuses rounds/
criteria writes for ``wf_status IN ('published', 'archived')`` (and also for
``wf_review IN ('in_review', 'approved')``), and ``workflows_published_immutable()``
already has an ``ELSIF OLD.status = 'archived'`` branch refusing any status
change or edit outside ``updated_at``/``deleted_at``
(``tests/integration/test_ph4_wave2_guarantees.py::test_an_archived_version_never_comes_back``
already covers exactly this, and passes on a fresh database at THIS branch's
merge base). A migration was drafted and applied here that re-created both
functions from the OLDER ``f3b5d7a9c1e4`` bodies plus a narrower archived
clause — that silently reverted the O6 INSERT-time draft check, the
submitted_by_user_id/review-status state machine, and the review-status
freeze on children, breaking 21 tests in
``test_ph4_wave2_guarantees.py``/``test_ph4_d4_guarantees.py``. It has been
DELETED rather than corrected: there is nothing left for E4 to add here, only
to depend on and verify, which the tests below do.

Reuses PH5 Wave 1's fixture builders (``Seed``, ``_seed_company``,
``_application``, ``_transition``, ``_hire``, ``_human_scorecard``,
``_hire_checkin``, ``_reject``) rather than re-deriving them, on the same
"one seeding vocabulary" precedent that module already established for its
own many flag-semantics tests.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from shared.db.engine import build_engine, build_session_factory
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.interviewer_scorecards import RequestMeta
from app.metrics.compute import CohortWindow, FunnelFilters, compute_funnel
from app.metrics.definitions import INTERVIEWER_SCORE_BAND_SQL
from app.panel_workload import PanelError, calibration, judgements
from tests.integration.test_ph5_w1_metrics import (  # noqa: F401 — fixture builders reused by name
    Seed,
    _application,
    _hire,
    _hire_checkin,
    _human_scorecard,
    _seed_company,
)

pytestmark = pytest.mark.integration

_META = RequestMeta(ip_address="127.0.0.1", user_agent="pytest")


# ---------------------------------------------------------------------------
# Fixtures — the SAME shape as tests/integration/test_ph5_w1_metrics.py's,
# defined again here (not imported) so a test function's own `db`/
# `committed_db`/`client` parameter is a plain fixture reference, never a
# name ruff sees as "redefining" a cross-module import (F811).
# ---------------------------------------------------------------------------
@pytest_asyncio.fixture
async def db() -> AsyncIterator[AsyncSession]:
    """A session whose writes roll back at the end of the test."""
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


@pytest_asyncio.fixture
async def committed_db() -> AsyncIterator[AsyncSession]:
    """A session that COMMITS — for tests that also drive the ASGI app,
    which reads through its OWN connection and cannot see an uncommitted
    write on this one."""
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


async def _grant_role(db: AsyncSession, user_id: uuid.UUID, role_name: str) -> None:
    await db.execute(
        text(
            "INSERT INTO user_roles (user_id, role_id, assigned_at)"
            " SELECT :u, id, now() FROM roles WHERE name = :r"
        ),
        {"u": user_id, "r": role_name},
    )


async def _add_round(
    db: AsyncSession, seed: Seed, *, position: int = 1, competency_id: str = "communication",
    title: str = "Second round",
) -> uuid.UUID:
    """A SECOND round on the same workflow as ``seed.round_id`` — its own
    frozen criterion, sharing the SAME ``competency_id`` on purpose (the O5
    gap #1 scenario: a role-engine competency string reused across rounds)."""
    workflow_id = await db.scalar(
        text("SELECT workflow_id FROM workflow_rounds WHERE id = :r"), {"r": seed.round_id},
    )
    round_id = uuid.uuid4()
    await db.execute(
        text(
            "INSERT INTO workflow_rounds (id, company_id, workflow_id, position, title, kind,"
            " created_at, updated_at) VALUES (:rd, :c, :w, :pos, :t, 'human_review', now(), now())"
        ),
        {"rd": round_id, "c": seed.company, "w": workflow_id, "pos": position, "t": title},
    )
    await db.execute(
        text(
            "INSERT INTO round_criteria (id, company_id, round_id, competency_id,"
            " competency_name, weight, created_at)"
            " VALUES (:id, :c, :rd, :comp, 'Communication', 1.0, now())"
        ),
        {"id": uuid.uuid4(), "c": seed.company, "rd": round_id, "comp": competency_id},
    )
    return round_id


async def _panel(
    db: AsyncSession, seed: Seed, round_id: uuid.UUID, peer: uuid.UUID, *,
    n: int, gen_score: int, peer_score: int,
) -> list[uuid.UUID]:
    """``n`` candidates, each scored by both ``seed.interviewer`` and
    ``peer`` on ``round_id`` — the same fixed-score-per-interviewer pattern
    ``tests/unit/test_ph4_wave3.py``'s ``_rows`` uses, at a real database."""
    enrolments = []
    for _ in range(n):
        e = await _application(db, seed)
        # Round on the human_scorecard rows: _human_scorecard always scores
        # seed.round_id, so re-target it for a second round by writing
        # directly.
        sc1 = uuid.uuid4()
        await db.execute(
            text(
                "INSERT INTO interviewer_scorecards (id, company_id, enrolment_id, round_id,"
                " interviewer_user_id, status, created_at, updated_at)"
                " VALUES (:i, :c, :e, :r, :iv, 'in_progress', now(), now())"
            ),
            {"i": sc1, "c": seed.company, "e": e, "r": round_id, "iv": seed.interviewer},
        )
        await db.execute(
            text(
                "INSERT INTO interviewer_scorecard_scores (scorecard_id, company_id, round_id,"
                " competency_id, score, not_assessed, updated_at)"
                " VALUES (:sc, :c, :r, 'communication', :s, false, now())"
            ),
            {"sc": sc1, "c": seed.company, "r": round_id, "s": gen_score},
        )
        await db.execute(
            text("UPDATE interviewer_scorecards SET status='submitted', submitted_at=now()"
                 " WHERE id = :i"), {"i": sc1},
        )
        sc2 = uuid.uuid4()
        await db.execute(
            text(
                "INSERT INTO interviewer_scorecards (id, company_id, enrolment_id, round_id,"
                " interviewer_user_id, status, created_at, updated_at)"
                " VALUES (:i, :c, :e, :r, :iv, 'in_progress', now(), now())"
            ),
            {"i": sc2, "c": seed.company, "e": e, "r": round_id, "iv": peer},
        )
        await db.execute(
            text(
                "INSERT INTO interviewer_scorecard_scores (scorecard_id, company_id, round_id,"
                " competency_id, score, not_assessed, updated_at)"
                " VALUES (:sc, :c, :r, 'communication', :s, false, now())"
            ),
            {"sc": sc2, "c": seed.company, "r": round_id, "s": peer_score},
        )
        await db.execute(
            text("UPDATE interviewer_scorecards SET status='submitted', submitted_at=now()"
                 " WHERE id = :i"), {"i": sc2},
        )
        enrolments.append(e)
    return enrolments


def _window() -> tuple[datetime, datetime]:
    now = datetime.now(tz=UTC)
    return now - timedelta(days=30), now + timedelta(days=1)


# ===========================================================================
# A suppressed criterion baseline withholds not_assessed too (security review)
# ===========================================================================
async def test_suppressed_criterion_baseline_also_nulls_not_assessed(db: AsyncSession) -> None:
    """`not_assessed` is the SAME shape of count over the SAME too-small
    population as `scores` — it must be withheld alongside it, not published
    one line below the comment explaining why `scores` is withheld."""
    seed = await _seed_company(db)
    await _grant_role(db, seed.hr, "hr_manager")
    peer = uuid.uuid4()
    await db.execute(
        text("INSERT INTO users (id, email, company_id) VALUES (:u, :e, :c)"),
        {"u": peer, "e": f"peer-{peer.hex[:8]}@w2.test", "c": seed.company},
    )

    # Only 3 candidates on this criterion — below min_candidates (5), so the
    # whole baseline is suppressed.
    for _ in range(2):
        e = await _application(db, seed)
        await _human_scorecard(db, seed, e, interviewer=seed.interviewer, score=4)
        await _human_scorecard(db, seed, e, interviewer=peer, score=4)

    # A third candidate where ONE interviewer marked the criterion
    # not_assessed — the scores trigger refuses an INSERT once the scorecard
    # is submitted, so the not_assessed row is added BEFORE submitting it
    # (the same in_progress-then-submit order `_human_scorecard` itself uses).
    e3 = await _application(db, seed)
    sc = uuid.uuid4()
    await db.execute(
        text(
            "INSERT INTO interviewer_scorecards (id, company_id, enrolment_id, round_id,"
            " interviewer_user_id, status, created_at, updated_at)"
            " VALUES (:i, :c, :e, :r, :iv, 'in_progress', now(), now())"
        ),
        {"i": sc, "c": seed.company, "e": e3, "r": seed.round_id, "iv": seed.interviewer},
    )
    await db.execute(
        text(
            "INSERT INTO interviewer_scorecard_scores (scorecard_id, company_id, round_id,"
            " competency_id, score, not_assessed, updated_at)"
            " VALUES (:sc, :c, :r, 'communication', NULL, true, now())"
        ),
        {"sc": sc, "c": seed.company, "r": seed.round_id},
    )
    await db.execute(
        text("UPDATE interviewer_scorecards SET status='submitted', submitted_at=now()"
             " WHERE id = :i"), {"i": sc},
    )
    await _human_scorecard(db, seed, e3, interviewer=peer, score=4)

    start, end = _window()
    out = await calibration(
        db, company_id=seed.company, start=start, end=end, requisition_id=None, round_id=None,
        actor=seed.hr, meta=_META,
    )
    (criterion,) = out["criteria"]
    assert criterion["candidates"] == 3
    assert criterion["suppressed"] is True
    assert criterion["scores"] is None
    assert criterion["not_assessed"] is None


# ===========================================================================
# Two rounds sharing a competency id stay separate
# ===========================================================================
async def test_two_rounds_sharing_a_competency_id_stay_separate(db: AsyncSession) -> None:
    seed = await _seed_company(db)
    peer = uuid.uuid4()
    await db.execute(
        text("INSERT INTO users (id, email, company_id) VALUES (:u, :e, :c)"),
        {"u": peer, "e": f"peer-{peer.hex[:8]}@w2.test", "c": seed.company},
    )
    await _grant_role(db, seed.interviewer, "interviewer")
    await _grant_role(db, peer, "interviewer")
    await _grant_role(db, seed.hr, "hr_manager")

    round_b = await _add_round(db, seed)
    # Round A: seed.interviewer consistently +2 over the peer.
    await _panel(db, seed, seed.round_id, peer, n=6, gen_score=5, peer_score=3)
    # Round B: SAME competency id, but no gap at all.
    await _panel(db, seed, round_b, peer, n=6, gen_score=3, peer_score=3)

    start, end = _window()
    out = await calibration(
        db, company_id=seed.company, start=start, end=end, requisition_id=None, round_id=None,
        actor=seed.hr, meta=_META,
    )
    interviewer_row = next(i for i in out["interviewers"] if i["user_id"] == str(seed.interviewer))
    by_key = {g["criterion_key"]: g for g in interviewer_row["by_criterion"]}
    assert by_key[f"{seed.round_id}:communication"]["signal"] == "higher"
    assert by_key[f"{round_b}:communication"]["signal"] is None

    criteria_by_key = {c["criterion_key"]: c for c in out["criteria"]}
    assert set(criteria_by_key) == {f"{seed.round_id}:communication", f"{round_b}:communication"}
    assert criteria_by_key[f"{seed.round_id}:communication"]["disagreement"] == pytest.approx(2.0)
    assert criteria_by_key[f"{round_b}:communication"]["disagreement"] == pytest.approx(0.0)


# ===========================================================================
# The archived-workflow freeze — PRE-EXISTING (PH4-O6, migration
# a2b4c6d8e0f1), not a new trigger. Calibration's frozen-criterion keying
# (round_id, competency_id) depends on this holding for every archived
# version it ever reads, so it is verified here rather than assumed; see the
# module docstring for why no new migration was needed or shipped.
# ===========================================================================
async def test_archived_workflow_round_criteria_update_is_refused(db: AsyncSession) -> None:
    seed = await _seed_company(db)
    await db.execute(
        text("SELECT workflow_id FROM workflow_rounds WHERE id = :r"), {"r": seed.round_id},
    )
    workflow_id = await db.scalar(
        text("SELECT workflow_id FROM workflow_rounds WHERE id = :r"), {"r": seed.round_id},
    )
    await db.execute(
        text("UPDATE workflows SET status = 'archived', updated_at = now() WHERE id = :w"),
        {"w": workflow_id},
    )
    with pytest.raises(DBAPIError, match="archived"):
        await db.execute(
            text("UPDATE round_criteria SET weight = 0.5 WHERE round_id = :r"),
            {"r": seed.round_id},
        )


async def test_archived_workflow_itself_cannot_change_status_again(db: AsyncSession) -> None:
    seed = await _seed_company(db)
    workflow_id = await db.scalar(
        text("SELECT workflow_id FROM workflow_rounds WHERE id = :r"), {"r": seed.round_id},
    )
    await db.execute(
        text("UPDATE workflows SET status = 'archived', updated_at = now() WHERE id = :w"),
        {"w": workflow_id},
    )
    with pytest.raises(DBAPIError, match="archived"):
        await db.execute(
            text("UPDATE workflows SET status = 'draft' WHERE id = :w"), {"w": workflow_id},
        )


async def test_draft_workflow_criteria_are_still_freely_editable(db: AsyncSession) -> None:
    """The freeze does not reach past published/archived (and review states
    in_review/approved) — a plain DRAFT workflow's criteria stay editable."""
    seed = await _seed_company(db)
    await db.execute(
        text("UPDATE round_criteria SET weight = 0.42 WHERE round_id = :r"), {"r": seed.round_id},
    )
    weight = await db.scalar(
        text("SELECT weight FROM round_criteria WHERE round_id = :r"), {"r": seed.round_id},
    )
    assert float(weight) == pytest.approx(0.42)


# ===========================================================================
# Independence in the judgements drill-down
# ===========================================================================
async def test_independence_excludes_a_pair_the_viewer_still_owes(db: AsyncSession) -> None:
    seed = await _seed_company(db)
    await _grant_role(db, seed.interviewer, "interviewer")
    await _grant_role(db, seed.hr, "hr_manager")

    visible = await _application(db, seed)
    await _human_scorecard(db, seed, visible, interviewer=seed.interviewer, score=5)

    blocked = await _application(db, seed)
    await _human_scorecard(db, seed, blocked, interviewer=seed.interviewer, score=1)
    # The VIEWER (seed.hr) still owes their own scorecard for `blocked`.
    await db.execute(
        text(
            "INSERT INTO interviewer_scorecards (id, company_id, enrolment_id, round_id,"
            " interviewer_user_id, status, created_at, updated_at)"
            " VALUES (:i, :c, :e, :r, :iv, 'assigned', now(), now())"
        ),
        {"i": uuid.uuid4(), "c": seed.company, "e": blocked, "r": seed.round_id, "iv": seed.hr},
    )

    start, end = _window()
    # 5 not reached: suppressed, but the enrolment SET must exclude `blocked`.
    for _ in range(4):
        e = await _application(db, seed)
        await _human_scorecard(db, seed, e, interviewer=seed.interviewer, score=5)

    out = await calibration(
        db, company_id=seed.company, start=start, end=end, requisition_id=None, round_id=None,
        actor=seed.hr, meta=_META,
    )
    row = next(i for i in out["interviewers"] if i["user_id"] == str(seed.interviewer))
    # 5 candidates visible (visible + the 4 extra), `blocked` excluded, so the
    # candidate count must be 5, not 6.
    assert row["candidates"] == 5


# ===========================================================================
# Tenant isolation
# ===========================================================================
async def test_judgements_404s_for_another_companys_interviewer(db: AsyncSession) -> None:
    seed_a = await _seed_company(db)
    seed_b = await _seed_company(db)
    await _grant_role(db, seed_a.hr, "hr_manager")
    await _grant_role(db, seed_b.interviewer, "interviewer")

    start, end = _window()
    with pytest.raises(PanelError) as exc_info:
        await judgements(
            db, company_id=seed_a.company, interviewer_id=seed_b.interviewer, start=start,
            end=end, requisition_id=None, round_id=None, criterion_key=None, actor=seed_a.hr,
            meta=_META,
        )
    assert exc_info.value.status_code == 404


async def test_calibration_never_sees_another_companys_scorecards(db: AsyncSession) -> None:
    seed_a = await _seed_company(db)
    seed_b = await _seed_company(db)
    await _grant_role(db, seed_a.hr, "hr_manager")
    await _grant_role(db, seed_a.interviewer, "interviewer")
    await _grant_role(db, seed_b.interviewer, "interviewer")

    for _ in range(5):
        e = await _application(db, seed_b)
        await _human_scorecard(db, seed_b, e, interviewer=seed_b.interviewer, score=5)

    start, end = _window()
    out = await calibration(
        db, company_id=seed_a.company, start=start, end=end, requisition_id=None, round_id=None,
        actor=seed_a.hr, meta=_META,
    )
    assert out["interviewers"] == []


# ===========================================================================
# The drill-down never shows a not-yet-shared judgement as agreement
# ===========================================================================
async def test_judgements_excludes_rows_nobody_else_has_scored_yet(db: AsyncSession) -> None:
    """5 candidates scored ONLY by seed.interviewer (panel_size 1 — nobody
    else has scored them yet, so their gap would compute to exactly 0.00,
    which reads as "agreed with the panel" rather than "not yet shared") plus
    1 candidate a peer ALSO scored (panel_size 2, a real shared judgement).
    The drill-down must return only the shared one."""
    seed = await _seed_company(db)
    await _grant_role(db, seed.hr, "hr_manager")
    await _grant_role(db, seed.interviewer, "interviewer")
    peer = uuid.uuid4()
    await db.execute(
        text("INSERT INTO users (id, email, company_id) VALUES (:u, :e, :c)"),
        {"u": peer, "e": f"peer-{peer.hex[:8]}@w2.test", "c": seed.company},
    )

    for _ in range(5):
        e = await _application(db, seed)
        await _human_scorecard(db, seed, e, interviewer=seed.interviewer, score=4)

    shared = await _application(db, seed)
    await _human_scorecard(db, seed, shared, interviewer=seed.interviewer, score=4)
    await _human_scorecard(db, seed, shared, interviewer=peer, score=2)

    start, end = _window()
    out = await judgements(
        db, company_id=seed.company, interviewer_id=seed.interviewer, start=start, end=end,
        requisition_id=None, round_id=None, criterion_key=None, actor=seed.hr, meta=_META,
    )
    assert out["total"] == 1
    assert out["rows"][0]["enrolment_id"] == str(shared)


async def test_judgements_refuses_a_round_id_that_disagrees_with_criterion_key(
    db: AsyncSession,
) -> None:
    """An explicit 422, not left to the suppression check to fail closed on
    (code review fix): round_id and the round embedded in criterion_key must
    agree, or the query would silently filter by the wrong round."""
    seed = await _seed_company(db)
    await _grant_role(db, seed.hr, "hr_manager")
    await _grant_role(db, seed.interviewer, "interviewer")
    other_round = uuid.uuid4()

    start, end = _window()
    with pytest.raises(PanelError) as exc_info:
        await judgements(
            db, company_id=seed.company, interviewer_id=seed.interviewer, start=start, end=end,
            requisition_id=None, round_id=other_round,
            criterion_key=f"{seed.round_id}:communication", actor=seed.hr, meta=_META,
        )
    assert exc_info.value.status_code == 422
    assert "round_id" in exc_info.value.detail


# ===========================================================================
# Erasure (E4 #15) — inherited by construction, not special-cased.
# Calibration copies nothing and names a candidate only by reading the LIVE
# applicant row, which erasure anonymises in place; this proves the
# inheritance holds rather than implementing anything new.
# ===========================================================================
async def test_an_erased_candidate_still_counts_and_reads_as_redacted(db: AsyncSession) -> None:
    seed = await _seed_company(db)
    await _grant_role(db, seed.hr, "hr_manager")
    await _grant_role(db, seed.interviewer, "interviewer")
    peer = uuid.uuid4()
    await db.execute(
        text("INSERT INTO users (id, email, company_id) VALUES (:u, :e, :c)"),
        {"u": peer, "e": f"peer-{peer.hex[:8]}@w2.test", "c": seed.company},
    )

    enrolments: list[uuid.UUID] = []
    for _ in range(5):
        e = await _application(db, seed)
        await _human_scorecard(db, seed, e, interviewer=seed.interviewer, score=5)
        await _human_scorecard(db, seed, e, interviewer=peer, score=3)
        enrolments.append(e)

    erased_enrolment = enrolments[0]
    erased_applicant_id = await db.scalar(
        text("SELECT applicant_id FROM enrolments WHERE id = :e"), {"e": erased_enrolment},
    )
    # Copied VERBATIM from services/admin_ops/app/erasure_executor.py's step 6
    # (the applicant-anonymisation UPDATE) — same SET clause, same literal
    # '[redacted]' — targeting by applicant id directly, since this test
    # exercises calibration's READ side of erasure, not the erasure REQUEST
    # flow that resolves a user_id to it.
    await db.execute(
        text(
            "UPDATE applicants "
            "SET full_name = '[redacted]', "
            "    email = NULL, "
            "    resume_text = NULL, "
            "    resume_s3_key = NULL, "
            "    embedding = NULL, "
            "    user_id = NULL, "
            "    phone = NULL, "
            "    years_experience = NULL, "
            "    current_company = NULL, "
            "    current_title = NULL, "
            "    linkedin_url = NULL, "
            "    github_url = NULL, "
            "    parsed_full_name = NULL, "
            "    parsed_email = NULL, "
            "    updated_at = now() "
            "WHERE id = :aid"
        ),
        {"aid": erased_applicant_id},
    )

    start, end = _window()
    out = await calibration(
        db, company_id=seed.company, start=start, end=end, requisition_id=None, round_id=None,
        actor=seed.hr, meta=_META,
    )
    row = next(i for i in out["interviewers"] if i["user_id"] == str(seed.interviewer))
    # The erased candidate's SCORE survives erasure by design (DATA-FLOW.md
    # :79): the aggregate still counts them as a judgement, unaffected.
    assert row["candidates"] == 5
    assert not row["suppressed"]

    drilldown = await judgements(
        db, company_id=seed.company, interviewer_id=seed.interviewer, start=start, end=end,
        requisition_id=None, round_id=None, criterion_key=None, actor=seed.hr, meta=_META,
    )
    assert drilldown["total"] == 5
    rows_by_enrolment = {r["enrolment_id"]: r for r in drilldown["rows"]}
    erased_row = rows_by_enrolment[str(erased_enrolment)]
    assert erased_row["candidate_name"] == "[redacted]"
    # Not a blanket redaction: the OTHER four rows still show their real
    # (test-fixture) names, proving only the erased candidate is affected.
    other_rows = [r for eid, r in rows_by_enrolment.items() if eid != str(erased_enrolment)]
    assert len(other_rows) == 4
    assert all(r["candidate_name"].startswith("Applicant ") for r in other_rows)

    audit = (
        await db.execute(
            text(
                "SELECT details FROM audit_log WHERE action = 'panel.calibration.evidence_viewed'"
                " ORDER BY event_ts DESC LIMIT 1"
            )
        )
    ).mappings().first()
    assert audit is not None
    details_text = str(audit["details"])
    assert "[redacted]" not in details_text
    assert "Applicant" not in details_text
    assert str(erased_applicant_id) not in details_text
    assert str(erased_enrolment) not in details_text


# ===========================================================================
# The band edges — the REAL registry SQL, not a Python re-implementation of
# it asserted against itself (code review fix; see the removed
# tests/unit/test_ph5_w2_calibration.py::test_band_edges_cover_the_whole_scale).
# ===========================================================================
@pytest.mark.parametrize(
    ("value", "band"),
    [(None, "none"), (1.0, "below_3"), (2.99, "below_3"), (3.0, "3_to_4"),
     (3.99, "3_to_4"), (4.0, "4_plus"), (5.0, "4_plus")],
)
async def test_band_edges_match_the_real_registry_sql(
    db: AsyncSession, value: float | None, band: str,
) -> None:
    """Executes app.metrics.definitions.INTERVIEWER_SCORE_BAND_SQL itself
    against Postgres, aliasing a bound value to the one column it may
    reference (m_hire_interviewer_score) — never re-typing the CASE-WHEN
    logic in Python."""
    result = await db.scalar(
        text(
            f"SELECT ({INTERVIEWER_SCORE_BAND_SQL})"  # nosec B608 — module constant, no request value
            " FROM (SELECT CAST(:val AS numeric) AS m_hire_interviewer_score) t"
        ),
        {"val": value},
    )
    assert result == band


# ===========================================================================
# Outcome linkage
# ===========================================================================
async def test_hires_band_by_current_human_interviewer_score(db: AsyncSession) -> None:
    seed = await _seed_company(db)
    now = datetime.now(tz=UTC)

    # 4_plus band: 5 hires scored 5, all retained.
    for _ in range(5):
        e = await _application(db, seed)
        await _human_scorecard(db, seed, e, score=5)
        await _hire(db, seed, e, hired_at=now - timedelta(days=100))
        await _hire_checkin(db, seed, e, employment="employed", performance="meets")

    # below_3 band: 5 hires scored 2, all left — a DIFFERENT retention figure,
    # each with its own check-in (>= min_cell, so T2-1b's uniform-suppression
    # rule does not blank BOTH groups out just because one is thin).
    for _ in range(5):
        e = await _application(db, seed)
        await _human_scorecard(db, seed, e, score=2)
        await _hire(db, seed, e, hired_at=now - timedelta(days=100))
        await _hire_checkin(db, seed, e, employment="left", performance=None, left_reason="voluntary")

    result = await compute_funnel(
        db, company_id=seed.company, cohort=CohortWindow(basis="hire"), filters=FunnelFilters(),
        group_by="interviewer_score_band",
    )
    groups = {g.key: g for g in result.groups}
    assert groups["4_plus"].metrics["hires"]["value"] == 5
    assert groups["below_3"].metrics["hires"]["value"] == 5
    assert groups["4_plus"].metrics["retention_90d"]["value"] == 100.0
    assert groups["4_plus"].metrics["retention_90d"]["suppressed"] is False
    assert groups["below_3"].metrics["retention_90d"]["value"] == 0.0
    assert groups["4_plus"].metrics["checkin_coverage"]["numerator"] == 5


async def test_a_reversed_hire_is_excluded_from_every_band(db: AsyncSession) -> None:
    seed = await _seed_company(db)
    now = datetime.now(tz=UTC)
    for _ in range(5):
        e = await _application(db, seed)
        await _human_scorecard(db, seed, e, score=5)
        await _hire(db, seed, e, hired_at=now - timedelta(days=100))

    reversed_hire = await _application(db, seed)
    await _human_scorecard(db, seed, reversed_hire, score=5)
    await _hire(db, seed, reversed_hire, hired_at=now - timedelta(days=100),
                offer_outcome="offer_declined")

    result = await compute_funnel(
        db, company_id=seed.company, cohort=CohortWindow(basis="hire"), filters=FunnelFilters(),
        group_by="interviewer_score_band",
    )
    groups = {g.key: g for g in result.groups}
    # Only the 5 standing hires count — the reversed one never enters the
    # `hire` cohort at all (f_hired is false for it).
    assert groups["4_plus"].metrics["hires"]["value"] == 5


async def test_a_hire_with_only_an_ai_interview_lands_in_none(db: AsyncSession) -> None:
    """AI interview scores never feed this measure at all — a hire with no
    CURRENT human scorecard lands in `none`, whatever the AI interview says."""
    seed = await _seed_company(db)
    now = datetime.now(tz=UTC)
    for _ in range(5):
        e = await _application(db, seed)
        await _human_scorecard(db, seed, e, score=5)
        await _hire(db, seed, e, hired_at=now - timedelta(days=100))

    ai_only = await _application(db, seed)
    await _hire(db, seed, ai_only, hired_at=now - timedelta(days=100))

    result = await compute_funnel(
        db, company_id=seed.company, cohort=CohortWindow(basis="hire"), filters=FunnelFilters(),
        group_by="interviewer_score_band",
    )
    groups = {g.key: g for g in result.groups}
    assert groups["none"].metrics["hires"]["value"] == 1


async def test_a_corrected_scorecard_uses_the_current_version(db: AsyncSession) -> None:
    seed = await _seed_company(db)
    now = datetime.now(tz=UTC)
    hired = []
    for _ in range(5):
        e = await _application(db, seed)
        await _human_scorecard(db, seed, e, score=5)
        await _hire(db, seed, e, hired_at=now - timedelta(days=100))
        hired.append(e)

    corrected = await _application(db, seed)
    original = await _human_scorecard(db, seed, corrected, score=1)
    # The unique-live index (uq_interviewer_scorecards_live) admits only ONE
    # row per (round, enrolment, interviewer) with superseded_at IS NULL —
    # the original must be superseded BEFORE the correction is inserted, the
    # same order the real correction endpoint uses.
    replacement_id = uuid.uuid4()
    await db.execute(
        text(
            "UPDATE interviewer_scorecards SET superseded_at = now(), superseded_by_id = :r"
            " WHERE id = :o"
        ),
        {"r": replacement_id, "o": original},
    )
    await _human_scorecard(
        db, seed, corrected, score=5, corrects_id=original, scorecard_id=replacement_id,
    )
    await _hire(db, seed, corrected, hired_at=now - timedelta(days=100))

    result = await compute_funnel(
        db, company_id=seed.company, cohort=CohortWindow(basis="hire"), filters=FunnelFilters(),
        group_by="interviewer_score_band",
    )
    groups = {g.key: g for g in result.groups}
    # All 6 hires (5 + the corrected one) score 5 on their CURRENT scorecard.
    assert groups["4_plus"].metrics["hires"]["value"] == 6


async def test_deleting_a_checkin_changes_the_coverage_count(db: AsyncSession) -> None:
    seed = await _seed_company(db)
    now = datetime.now(tz=UTC)
    enrolments = []
    for _ in range(5):
        e = await _application(db, seed)
        await _human_scorecard(db, seed, e, score=5)
        await _hire(db, seed, e, hired_at=now - timedelta(days=100))
        await _hire_checkin(db, seed, e)
        enrolments.append(e)

    before = await compute_funnel(
        db, company_id=seed.company, cohort=CohortWindow(basis="hire"), filters=FunnelFilters(),
        group_by="interviewer_score_band",
    )
    before_covered = {g.key: g for g in before.groups}["4_plus"].metrics["checkin_coverage"]["numerator"]
    assert before_covered == 5

    await db.execute(text("DELETE FROM hire_checkins WHERE enrolment_id = :e"), {"e": enrolments[0]})

    after = await compute_funnel(
        db, company_id=seed.company, cohort=CohortWindow(basis="hire"), filters=FunnelFilters(),
        group_by="interviewer_score_band",
    )
    after_covered = {g.key: g for g in after.groups}["4_plus"].metrics["checkin_coverage"]["numerator"]
    assert after_covered == 4


async def test_a_band_under_the_floor_returns_null_not_a_value(db: AsyncSession) -> None:
    seed = await _seed_company(db)
    now = datetime.now(tz=UTC)
    for _ in range(5):
        e = await _application(db, seed)
        await _human_scorecard(db, seed, e, score=5)
        await _hire(db, seed, e, hired_at=now - timedelta(days=100))
        await _hire_checkin(db, seed, e)

    # Only 3 hires in below_3 — under the min_cell floor of 5.
    for _ in range(3):
        e = await _application(db, seed)
        await _human_scorecard(db, seed, e, score=2)
        await _hire(db, seed, e, hired_at=now - timedelta(days=100))
        await _hire_checkin(db, seed, e)

    result = await compute_funnel(
        db, company_id=seed.company, cohort=CohortWindow(basis="hire"), filters=FunnelFilters(),
        group_by="interviewer_score_band",
    )
    groups = {g.key: g for g in result.groups}
    below = groups["below_3"].metrics["retention_90d"]
    assert below["suppressed"] is True
    assert below["value"] is None
    # Counts (hires) are never suppressed.
    assert groups["below_3"].metrics["hires"]["value"] == 3


# ===========================================================================
# Cohort windows
# ===========================================================================
async def test_hire_cohort_window_is_the_first_move_into_hired(db: AsyncSession) -> None:
    seed = await _seed_company(db)
    now = datetime.now(tz=UTC)
    in_window = await _application(db, seed)
    await _human_scorecard(db, seed, in_window, score=5)
    await _hire(db, seed, in_window, hired_at=now - timedelta(days=5))

    outside_window = await _application(db, seed)
    await _human_scorecard(db, seed, outside_window, score=5)
    await _hire(db, seed, outside_window, hired_at=now - timedelta(days=400))

    result = await compute_funnel(
        db, company_id=seed.company,
        cohort=CohortWindow(basis="hire", from_=(now - timedelta(days=30)).date(), to_=None),
        filters=FunnelFilters(), group_by="interviewer_score_band",
    )
    groups = {g.key: g for g in result.groups}
    assert groups["4_plus"].metrics["hires"]["value"] == 1


# ===========================================================================
# Consistency: outcome-signals "All" hires == the funnel's hires
# ===========================================================================
async def test_outcome_signals_all_hires_matches_the_funnel(
    committed_db: AsyncSession, client: AsyncClient,
) -> None:
    from tests.integration.test_ph5_w1_metrics import _auth

    seed = await _seed_company(committed_db)
    now = datetime.now(tz=UTC)
    for _ in range(5):
        e = await _application(committed_db, seed)
        await _human_scorecard(committed_db, seed, e, score=5)
        await _hire(committed_db, seed, e, hired_at=now - timedelta(days=100))
    await committed_db.commit()

    headers = _auth(seed.hr, ["hr_manager"])
    funnel = await client.get("/hr/analytics/funnel", headers=headers, params={"cohort": "hire"})
    outcome = await client.get("/hr/analytics/outcome-signals", headers=headers,
                                params={"cohort": "hire"})
    assert funnel.status_code == 200 and outcome.status_code == 200
    funnel_hires = funnel.json()["groups"][0]["metrics"]["hires"]["value"]
    outcome_hires = next(g for g in outcome.json()["groups"] if g["key"] is None)["metrics"]["hires"]["value"]
    assert funnel_hires == outcome_hires == 5


# ===========================================================================
# HTTP smoke: roles, no writes, the audit row
# ===========================================================================
async def test_interviewer_and_super_admin_get_403_on_outcome_signals(
    committed_db: AsyncSession, client: AsyncClient,
) -> None:
    from tests.integration.test_ph5_w1_metrics import _auth

    seed = await _seed_company(committed_db)
    await committed_db.commit()

    for user_id, role in ((seed.interviewer, "interviewer"), (seed.admin, "super_admin")):
        resp = await client.get(
            "/hr/analytics/outcome-signals", headers=_auth(user_id, [role]),
        )
        assert resp.status_code == 403, role


async def test_the_judgements_drilldown_audit_row_carries_no_candidate_names(
    committed_db: AsyncSession, client: AsyncClient,
) -> None:
    from tests.integration.test_ph5_w1_metrics import _auth

    seed = await _seed_company(committed_db)
    await _grant_role(committed_db, seed.interviewer, "interviewer")
    for _ in range(6):
        e = await _application(committed_db, seed)
        await _human_scorecard(committed_db, seed, e, interviewer=seed.interviewer, score=5)
    await committed_db.commit()

    headers = _auth(seed.hr, ["hr_manager"])
    resp = await client.get(
        "/hr/panel/calibration/judgements", headers=headers,
        params={"interviewer_id": str(seed.interviewer)},
    )
    # Not suppressed: candidates=6 >= min_candidates, and no criterion_key was
    # requested (only the interviewer-level suppression gate applies here).
    assert resp.status_code == 200, resp.text

    audit = (
        await committed_db.execute(
            text(
                "SELECT details FROM audit_log WHERE action = 'panel.calibration.evidence_viewed'"
                " ORDER BY event_ts DESC LIMIT 1"
            )
        )
    ).mappings().first()
    # Assert the row exists FIRST — a version of this test that only checked
    # candidate-name absence when a row happened to exist would still pass if
    # the audit write were deleted entirely (code review fix).
    assert audit is not None, "panel.calibration.evidence_viewed was not audited at all"
    details_text = str(audit["details"])
    assert "Applicant" not in details_text
