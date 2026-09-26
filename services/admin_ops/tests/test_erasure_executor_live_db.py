"""The erasure executor against a REAL, migrated Postgres — security review
finding S5-004-MEDIUM.

WHY THIS FILE EXISTS
--------------------
``services/admin_ops`` had NO database fixture at all before this file — its
entire suite (``test_erasure_executor.py`` included) is mock-based: a fake
``AsyncSession`` that dispatches on the SQL statement's own text. Those tests
prove the executor RUNS the statements a reviewer expects; they cannot prove
those statements do what they claim against a real schema, real foreign keys
and real triggers.

The auditor's concrete example was step 5k (PH5-E3 talent-pool memberships,
which MUST be deleted before ``applicants.user_id`` is nulled by step 6): the
only things that stood behind that ordering were (a) a SOURCE-POSITION
assertion in ``test_erasure_step_order.py`` — the DELETE text appears earlier
in the file than the UPDATE text — and (b) a DIFFERENT service's test
(``services/data_gateway/tests/integration/test_ph5_e3_pools_db.py``) that
HAND-COPIES the DELETE statement as a Python string literal rather than
importing or running the real code. A change to the real statement's
``WHERE`` clause that kept the substrings ``applicant_id IN`` and ``FROM
applicants WHERE user_id = :uid`` intact but was otherwise wrong — a bad
join, a swapped bind — would be caught by NEITHER: the source-position test
never reads the clause, and the hand-copied string cannot diverge from
itself.

This file runs ``app.erasure_executor._execute_one_erasure`` ITSELF — the
real function, unmocked — against a real, migrated database, seeding a
subject with a row in every table step 5k and its neighbours touch (including
``talent_pool_members``) and asserting the real rows are gone afterwards, and
that the tables ``EXCLUDED_TABLES`` declares survive.

WHAT THIS DOES NOT ALSO DO
---------------------------
Re-prove every one of the ~30 erasure steps at the SQL-shape level — that is
what ``test_erasure_executor.py``'s mock suite already does, exhaustively,
and mutation-checked. This file's job is narrower and specific: prove the
statements actually execute correctly against a real schema, with real
foreign keys enforcing the ordering the comments describe, for a
representative core of tables (turns, resumes, scorecards, sessions,
notifications, applicants, users) plus the one table security review named by
name.

WHY ``app.s3_client.delete_objects`` IS PATCHED
------------------------------------------------
``resumes.resume_s3_key`` is NOT NULL at the schema level, so the seeded
subject cannot avoid collecting at least one S3 key (the mock suite's
``_fake_delete_objects`` precedent). This file is about the DATABASE side of
the executor — real rows, real foreign keys, real triggers — and exercising
the S3 phase for real would need a live bucket, a different concern from the
one the audit finding raised. ``delete_objects`` is therefore patched to
report every key it is handed as deleted, exactly as the existing mock suite
already does; nothing about the DATABASE assertions below goes through a
mock.

FIXTURE STYLE
-------------
On ``services/data_gateway/tests/integration/test_ph5_e2_corpus_http.py``'s
``committed_db`` precedent: a session that COMMITS. ``_execute_one_erasure``
itself never commits (its docstring: "the caller owns the commit / rollback
decision"), so this file commits explicitly after calling it, then reads back
through the SAME session to assert — real rows, real constraints, not a
mock's memory of what it was asked to run.

``pytestmark = pytest.mark.integration`` — CI's ``-m "not integration"`` unit
leg skips this file, exactly like data_gateway's DB-backed tests. See this
file's own comment further down, and the task report, for whether any CI leg
runs it WITH that marker — as of this writing, none does for admin_ops.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from shared.db.engine import build_engine, build_session_factory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.erasure_executor import _execute_one_erasure
from app.models import ErasureRequest

pytestmark = pytest.mark.integration

_SYSTEM_ACTOR = uuid.UUID("00000000-0000-0000-0000-000000000001")


class _FakeS3Settings:
    """Just enough of ``Settings`` for the S3 phase to read bucket names —
    see the module docstring's "WHY delete_objects IS PATCHED"."""

    s3_bucket_name = "test-uploads"
    s3_scorecard_bucket = "test-scorecards"


async def _fake_delete_objects(keys_by_bucket: dict[str, list[str]], *, settings: Any) -> int:
    """Reports every key handed to it as deleted — the mock suite's
    ``_fake_delete_objects`` contract (``test_erasure_executor.py``): the
    executor checks this return value before it is willing to stamp
    'completed', so a stub returning anything else would be the exact
    false-success that suite exists to catch."""
    return sum(len(keys) for keys in keys_by_bucket.values())


@pytest_asyncio.fixture
async def committed_db() -> AsyncIterator[AsyncSession]:
    """A session that COMMITS — the ``test_ph5_e2_corpus_http.py`` precedent.
    This repo's own disposable database (never ``.env``'s / ``space.env``'s
    ``DATABASE_URL`` — see ``shared.security``'s remote-database guard, which
    every service's config validates against in production/staging)."""
    engine = build_engine(
        database_url=settings.database_url, database_ssl=settings.database_ssl, pool_size=2,
    )
    factory = build_session_factory(engine)
    try:
        async with factory() as session:
            yield session
    finally:
        await engine.dispose()


class _Subject:
    """Every id this file seeds and later asserts against."""

    def __init__(self) -> None:
        self.company_id = uuid.uuid4()
        self.user_id = uuid.uuid4()
        self.other_user_id = uuid.uuid4()  # the isolation test's second subject
        self.job_id = uuid.uuid4()
        self.session_id = uuid.uuid4()
        self.applicant_id = uuid.uuid4()
        self.other_applicant_id = uuid.uuid4()
        self.pool_id = uuid.uuid4()
        self.member_id = uuid.uuid4()
        self.other_member_id = uuid.uuid4()
        self.request_id = uuid.uuid4()
        self.tag = self.user_id.hex[:10]


async def _seed(db: AsyncSession, s: _Subject, *, with_second_member: bool = False) -> None:
    """Seed one full erasure subject: a company, a user with PII on every
    column the executor anonymises, a session/turn/resume/scorecard,
    a notification, an applicant linked by ``user_id``, a talent pool and
    ONE live membership of it — the table the audit finding named.

    When ``with_second_member`` is set, a SECOND user/applicant/membership is
    seeded in the SAME pool and NEVER erased — the isolation test's control,
    proving the real WHERE clause reaches only the erased subject's row and
    not every membership of the pool.
    """
    now = datetime.now(tz=UTC)
    await db.execute(
        text("INSERT INTO companies (id, name, slug) VALUES (:c, 'Erasure Co', :slug)"),
        {"c": s.company_id, "slug": f"erasure-live-{s.tag}"},
    )
    await db.execute(
        text(
            "INSERT INTO users (id, email, full_name, phone, resume_text, resume_s3_key,"
            " linkedin_url, github_url, avatar_url, headline, bio, official_email,"
            " location, employment_status, desired_roles, onboarding_goal, target_role,"
            " password_hash, naipunyam_id)"
            " VALUES (:u, :e, 'Priya Sharma', '+91-9000000000', 'Some resume prose',"
            " NULL, 'https://linkedin.example/priya', 'https://github.example/priya',"
            " 'https://avatar.example/priya.png', 'Fitter, 4 yrs', 'A short bio',"
            " 'priya.official@example.com', 'Vizag', 'employed', 'Fitter;Welder',"
            " 'practice interviews', 'fitter', 'hash', :np)"
        ),
        {"u": s.user_id, "e": f"priya-{s.tag}@erasure.test", "np": f"np-{s.tag}"},
    )
    await db.execute(
        text(
            "INSERT INTO jobs (id, title, description, level)"
            " VALUES (:j, 'Fitter', 'A fitter role', 'mid')"
        ),
        {"j": s.job_id},
    )
    await db.execute(
        text(
            "INSERT INTO sessions (id, user_id, job_id, status)"
            " VALUES (:s, :u, :j, 'completed')"
        ),
        {"s": s.session_id, "u": s.user_id, "j": s.job_id},
    )
    await db.execute(
        text(
            "INSERT INTO turns (id, session_id, turn_number, speaker, text_content)"
            " VALUES (gen_random_uuid(), :s, 1, 'candidate', 'My candidate speech, verbatim')"
        ),
        {"s": s.session_id},
    )
    # resume_s3_key is NOT NULL at the schema level, so this row always
    # collects one S3 key — see the module docstring's "WHY delete_objects IS
    # PATCHED". applicants/users/scorecards/turns keys are all left NULL
    # (every one of those columns IS nullable), so this is the only key any
    # test in this file collects.
    await db.execute(
        text(
            "INSERT INTO resumes (id, user_id, filename, resume_text, resume_s3_key, is_current,"
            " uploaded_at)"
            " VALUES (gen_random_uuid(), :u, 'priya_cv.pdf', 'Resume prose, version 1',"
            " :key, true, :t)"
        ),
        {"u": s.user_id, "key": f"resumes/{s.user_id}/v1.pdf", "t": now},
    )
    await db.execute(
        text(
            "INSERT INTO scorecards (scorecard_id, session_id, scores, summary, lang)"
            " VALUES (gen_random_uuid(), :s, CAST('{\"technical\": 7}' AS jsonb),"
            " 'A solid interview', 'en')"
        ),
        {"s": s.session_id},
    )
    await db.execute(
        text(
            "INSERT INTO notifications (id, user_id, kind, title, body)"
            " VALUES (gen_random_uuid(), :u, 'welcome', 'Welcome, Priya!', 'Glad to have you')"
        ),
        {"u": s.user_id},
    )
    await db.execute(
        text(
            "INSERT INTO applicants (id, company_id, user_id, full_name, email,"
            " target_job_title, resume_text, resume_s3_key, phone, years_experience,"
            " current_company, current_title, linkedin_url, github_url,"
            " parsed_full_name, parsed_email)"
            " VALUES (:a, :c, :u, 'Priya Sharma', :e, 'Fitter', 'Applicant resume text', NULL,"
            " '+91-9000000000', 4, 'Acme Corp', 'Fitter', 'https://linkedin.example/priya',"
            " 'https://github.example/priya', 'Priya S', :e)"
        ),
        {"a": s.applicant_id, "c": s.company_id, "u": s.user_id, "e": f"priya-{s.tag}@erasure.test"},
    )
    await db.execute(
        text(
            "INSERT INTO talent_pools (id, company_id, name)"
            " VALUES (:p, :c, :n)"
        ),
        {"p": s.pool_id, "c": s.company_id, "n": f"Fitters live {s.tag}"},
    )
    await db.execute(
        text(
            "INSERT INTO talent_pool_members (id, company_id, pool_id, applicant_id, source, note)"
            " VALUES (:m, :c, :p, :a, 'manual', 'Strong fault diagnosis')"
        ),
        {"m": s.member_id, "c": s.company_id, "p": s.pool_id, "a": s.applicant_id},
    )

    if with_second_member:
        await db.execute(
            text("INSERT INTO users (id, email, full_name) VALUES (:u, :e, 'Bala Rao')"),
            {"u": s.other_user_id, "e": f"bala-{s.tag}@erasure.test"},
        )
        await db.execute(
            text(
                "INSERT INTO applicants (id, company_id, user_id, full_name, email,"
                " target_job_title)"
                " VALUES (:a, :c, :u, 'Bala Rao', :e, 'Fitter')"
            ),
            {"a": s.other_applicant_id, "c": s.company_id, "u": s.other_user_id,
             "e": f"bala-{s.tag}@erasure.test"},
        )
        await db.execute(
            text(
                "INSERT INTO talent_pool_members (id, company_id, pool_id, applicant_id, source)"
                " VALUES (:m, :c, :p, :a, 'manual')"
            ),
            {"m": s.other_member_id, "c": s.company_id, "p": s.pool_id, "a": s.other_applicant_id},
        )
    await db.commit()


async def _cleanup(db: AsyncSession, s: _Subject) -> None:
    """Deleting ``companies`` cascades to applicants/talent_pools/talent_pool_members
    and, via ``sessions``/``resumes``, everything keyed off the user — but the
    ``users`` rows themselves are NOT reached from ``companies`` (only
    ``created_by_user_id`` is, and this fixture never sets it), and
    ``erasure_requests``/``audit_log`` name the user directly. Order matters:
    ``erasure_requests.user_id`` is ON DELETE RESTRICT, so it goes before the
    user it names.
    """
    await db.execute(text("DELETE FROM erasure_requests WHERE user_id = ANY(:u)"),
                     {"u": [s.user_id, s.other_user_id]})
    await db.execute(text("DELETE FROM companies WHERE id = :c"), {"c": s.company_id})
    await db.execute(text("DELETE FROM users WHERE id = ANY(:u)"),
                     {"u": [s.user_id, s.other_user_id]})
    await db.commit()


async def _count(db: AsyncSession, sql: str, params: dict[str, Any]) -> int:
    return int(await db.scalar(text(sql), params) or 0)


# ---------------------------------------------------------------------------
# The main proof: erase one real subject, against real Postgres, no mocks.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_execute_one_erasure_against_real_postgres_erases_and_excludes_correctly(
    committed_db: AsyncSession,
) -> None:
    s = _Subject()
    await _seed(committed_db, s)
    try:
        await committed_db.execute(
            text(
                "INSERT INTO erasure_requests"
                " (request_id, user_id, requested_by, reason, status, scheduled_for, created_at)"
                " VALUES (:r, :u, :u, 'test erasure', 'pending', :sched, :created)"
            ),
            {"r": s.request_id, "u": s.user_id,
             "sched": datetime.now(tz=UTC) - timedelta(days=1),
             "created": datetime.now(tz=UTC) - timedelta(days=31)},
        )
        await committed_db.commit()

        request = ErasureRequest(
            request_id=s.request_id, user_id=s.user_id, requested_by=s.user_id,
            reason="test erasure", status="pending",
            scheduled_for=datetime.now(tz=UTC) - timedelta(days=1),
            completed_at=None, artifacts=None,
            created_at=datetime.now(tz=UTC) - timedelta(days=31),
        )

        with patch("app.s3_client.delete_objects", new=AsyncMock(side_effect=_fake_delete_objects)):
            artifacts = await _execute_one_erasure(
                db=committed_db, request=request, system_actor_id=_SYSTEM_ACTOR,
                settings=_FakeS3Settings(),  # type: ignore[arg-type]
            )
        await committed_db.commit()

        # -------------------------------------------------------------
        # ERASED_TABLES: hard-deleted rows are actually gone.
        # -------------------------------------------------------------
        assert await _count(
            committed_db, "SELECT count(*) FROM turns WHERE session_id = :s", {"s": s.session_id},
        ) == 0
        assert await _count(
            committed_db, "SELECT count(*) FROM resumes WHERE user_id = :u", {"u": s.user_id},
        ) == 0
        assert await _count(
            committed_db, "SELECT count(*) FROM scorecards WHERE session_id = :s",
            {"s": s.session_id},
        ) == 0
        assert await _count(
            committed_db, "SELECT count(*) FROM sessions WHERE user_id = :u", {"u": s.user_id},
        ) == 0
        assert await _count(
            committed_db, "SELECT count(*) FROM notifications WHERE user_id = :u",
            {"u": s.user_id},
        ) == 0
        # THE finding: talent_pool_members, deleted while it could still be
        # reached through applicants.user_id — proving the real statement's
        # ordering AND its WHERE clause both work against a real schema.
        assert await _count(
            committed_db, "SELECT count(*) FROM talent_pool_members WHERE id = :m",
            {"m": s.member_id},
        ) == 0

        # -------------------------------------------------------------
        # applicants: ANONYMISED, not deleted — every PII column nulled.
        # -------------------------------------------------------------
        applicant_row = (
            await committed_db.execute(
                text(
                    "SELECT full_name, email, resume_text, resume_s3_key, user_id, phone,"
                    " years_experience, current_company, current_title, linkedin_url,"
                    " github_url, parsed_full_name, parsed_email"
                    " FROM applicants WHERE id = :a"
                ),
                {"a": s.applicant_id},
            )
        ).mappings().first()
        assert applicant_row is not None, "the applicant row must survive, anonymised"
        assert applicant_row["full_name"] == "[redacted]"
        assert applicant_row["email"] is None
        assert applicant_row["resume_text"] is None
        assert applicant_row["resume_s3_key"] is None
        assert applicant_row["user_id"] is None
        assert applicant_row["phone"] is None
        assert applicant_row["years_experience"] is None
        assert applicant_row["current_company"] is None
        assert applicant_row["current_title"] is None
        assert applicant_row["linkedin_url"] is None
        assert applicant_row["github_url"] is None
        assert applicant_row["parsed_full_name"] is None
        assert applicant_row["parsed_email"] is None

        # -------------------------------------------------------------
        # users: ANONYMISED in place — the erasure_requests FK is RESTRICT,
        # so this row must survive, sentinel-valued.
        # -------------------------------------------------------------
        user_row = (
            await committed_db.execute(
                text(
                    "SELECT email, full_name, phone, resume_text, resume_s3_key,"
                    " password_hash, naipunyam_id, linkedin_url, github_url, avatar_url,"
                    " headline, bio, official_email, location, employment_status,"
                    " desired_roles, onboarding_goal, target_role"
                    " FROM users WHERE id = :u"
                ),
                {"u": s.user_id},
            )
        ).mappings().first()
        assert user_row is not None
        assert user_row["email"] == f"erased_{s.user_id}@deleted.invalid"
        assert user_row["full_name"] == "[redacted]"
        for col in (
            "phone", "resume_text", "resume_s3_key", "password_hash", "naipunyam_id",
            "linkedin_url", "github_url", "avatar_url", "headline", "bio", "official_email",
            "location", "employment_status", "desired_roles", "onboarding_goal", "target_role",
        ):
            assert user_row[col] is None, f"users.{col} must be nulled by erasure"

        # -------------------------------------------------------------
        # EXCLUDED_TABLES: these rows must survive untouched.
        # -------------------------------------------------------------
        assert await _count(
            committed_db, "SELECT count(*) FROM companies WHERE id = :c", {"c": s.company_id},
        ) == 1
        assert await _count(
            committed_db, "SELECT count(*) FROM talent_pools WHERE id = :p", {"p": s.pool_id},
        ) == 1

        req_row = (
            await committed_db.execute(
                text("SELECT status, completed_at, artifacts FROM erasure_requests"
                     " WHERE request_id = :r"),
                {"r": s.request_id},
            )
        ).mappings().first()
        assert req_row is not None, "erasure_requests is the §12 proof — never deleted"
        assert req_row["status"] == "completed"
        assert req_row["completed_at"] is not None
        assert req_row["artifacts"]["talent_pool_members_deleted"] == 1

        audit_count = await _count(
            committed_db,
            "SELECT count(*) FROM audit_log"
            " WHERE resource_id = :u AND action = 'dpdp_erasure_completed'",
            {"u": s.user_id},
        )
        assert audit_count == 1

        assert artifacts["talent_pool_members_deleted"] == 1
        assert artifacts["applicants_anonymised"] == 1
        assert artifacts["turns_deleted"] == 1
        assert artifacts["resumes_deleted"] == 1
        assert artifacts["scorecards_deleted"] == 1
        assert artifacts["sessions_deleted"] == 1
        assert artifacts["notifications_deleted"] == 1
        assert artifacts["resume_objects_deleted"] == 1
        assert artifacts["s3_objects_deleted"] == 1
    finally:
        await _cleanup(committed_db, s)


# ---------------------------------------------------------------------------
# The "bad join" the audit finding worried about: a second, un-erased member
# of the SAME pool must survive. Catches a WHERE clause that is too broad
# (deletes every membership of the pool) just as much as one that is too
# narrow (deletes nothing).
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_step_5k_does_not_touch_another_applicants_membership_in_the_same_pool(
    committed_db: AsyncSession,
) -> None:
    s = _Subject()
    await _seed(committed_db, s, with_second_member=True)
    try:
        request = ErasureRequest(
            request_id=uuid.uuid4(), user_id=s.user_id, requested_by=s.user_id,
            reason="test erasure", status="pending",
            scheduled_for=datetime.now(tz=UTC) - timedelta(days=1),
            completed_at=None, artifacts=None,
            created_at=datetime.now(tz=UTC) - timedelta(days=31),
        )
        with patch("app.s3_client.delete_objects", new=AsyncMock(side_effect=_fake_delete_objects)):
            await _execute_one_erasure(
                db=committed_db, request=request, system_actor_id=_SYSTEM_ACTOR,
                settings=_FakeS3Settings(),  # type: ignore[arg-type]
            )
        await committed_db.commit()

        assert await _count(
            committed_db, "SELECT count(*) FROM talent_pool_members WHERE id = :m",
            {"m": s.member_id},
        ) == 0, "the erased subject's own membership must be gone"
        assert await _count(
            committed_db, "SELECT count(*) FROM talent_pool_members WHERE id = :m",
            {"m": s.other_member_id},
        ) == 1, "a DIFFERENT applicant's membership of the SAME pool must survive"

        other_applicant = (
            await committed_db.execute(
                text("SELECT full_name, user_id FROM applicants WHERE id = :a"),
                {"a": s.other_applicant_id},
            )
        ).mappings().first()
        assert other_applicant is not None
        assert other_applicant["full_name"] == "Bala Rao", "the OTHER applicant must be untouched"
        assert other_applicant["user_id"] == s.other_user_id
    finally:
        await _cleanup(committed_db, s)
