"""SQLAlchemy ORM models for data_gateway.

Covers nine migrations:
  20260527_0001 — auth tables (users, roles, user_roles, dpdp_consent_ledger)
  20260527_0418 — interview data model (jobs, nos_competencies, sessions, turns)
  20260529_0001 — DPDP erasure (erasure_requests, audit_log, sessions.deleted_at)
  20260529_0002 — scorecards (scorecards table for S5-006 end-of-session scoring)
  20260529_0004 — interview context fields (B-033: linkedin_url, github_url on
                  users; company_name, department, interview_type on jobs)
  20260529_0005 — resume/JD document columns (B-031/B-032: resume_text,
                  resume_s3_key on users; jd_text, jd_s3_key on jobs)
  20260530_0001 — self-serve custom jobs (created_by_user_id UUID NULL FK→users)
  20260530_0002 — resume version history table (resumes)
  20260530_0003 — session presenter_id column (Area 4 UI redesign v2)
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    ARRAY,
    NUMERIC,
    TIMESTAMP,
    Boolean,
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# Auth tables (migration 0001)
# ---------------------------------------------------------------------------


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    password_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    full_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    phone: Mapped[str | None] = mapped_column(Text, nullable=True)
    preferred_language: Mapped[str] = mapped_column(Text, default="en", nullable=False)
    naipunyam_id: Mapped[str | None] = mapped_column(Text, unique=True, nullable=True)
    # B-033 — candidate profile enrichment
    linkedin_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    github_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    # B-031 — resume document storage (single-column for B-033 enrichment)
    resume_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    resume_s3_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Self-service profile (editable candidate / HR / admin profile).
    # avatar_url holds a small client-downscaled data URI or an external URL.
    avatar_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    headline: Mapped[str | None] = mapped_column(Text, nullable=True)
    bio: Mapped[str | None] = mapped_column(Text, nullable=True)
    employment_status: Mapped[str | None] = mapped_column(Text, nullable=True)
    desired_roles: Mapped[str | None] = mapped_column(Text, nullable=True)
    official_email: Mapped[str | None] = mapped_column(Text, nullable=True)
    location: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # Email system — verification + opt-in login alerts (migration 20260629_0002).
    # email_verified_at NULL = unverified (non-blocking: login still works).
    # notify_login_email — per-user opt-in for the "new sign-in" email alert.
    email_verified_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    notify_login_email: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    # HR workflow (Phase 0) — multi-tenant scoping + forced first-login reset.
    # company_id is NULL for platform_owner / platform users; it is SET for a
    # company super_admin and its hr_managers (they are company-scoped).
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("companies.id", ondelete="SET NULL"), nullable=True
    )
    must_change_password: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)

    user_roles: Mapped[list[UserRole]] = relationship("UserRole", back_populates="user")
    sessions: Mapped[list[Session]] = relationship("Session", back_populates="user")
    consents: Mapped[list[DpdpConsent]] = relationship("DpdpConsent", back_populates="user")
    resumes: Mapped[list[Resume]] = relationship(
        "Resume", back_populates="user", cascade="all, delete-orphan"
    )


class Role(Base):
    __tablename__ = "roles"

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    name: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    user_roles: Mapped[list[UserRole]] = relationship("UserRole", back_populates="role")


class UserRole(Base):
    __tablename__ = "user_roles"

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    role_id: Mapped[int] = mapped_column(
        SmallInteger, ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True
    )
    assigned_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))

    user: Mapped[User] = relationship("User", back_populates="user_roles")
    role: Mapped[Role] = relationship("Role", back_populates="user_roles")


class Company(Base):
    """A tenant company (HR workflow — Phase 0).

    Each company has one super_admin and any number of HR managers; all of their
    applicants / exams / jobs belong to the company. Companies are created and
    managed by the platform owner (platform_owner).
    """

    __tablename__ = "companies"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    slug: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )


class Applicant(Base):
    """An applicant being screened by an HR manager (HR workflow — Phase 1).

    Multi-tenant: every row belongs to one company; all HR queries filter by
    company_id. The role screened for is denormalized (target_*). The ATS score
    comes from feedback_billing's resume scorer.
    """

    __tablename__ = "applicants"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    # Phase 3: the minted guest_candidate user (erasure trace + redeem idempotency).
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    full_name: Mapped[str] = mapped_column(Text, nullable=False)
    email: Mapped[str | None] = mapped_column(Text, nullable=True)
    target_job_title: Mapped[str] = mapped_column(Text, nullable=False)
    target_level: Mapped[str] = mapped_column(Text, default="mid", nullable=False)
    target_jd_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    resume_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    resume_s3_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    ats_overall: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    ats_breakdown: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    ats_strengths: Mapped[list[Any] | None] = mapped_column(JSONB, nullable=True)
    ats_concerns: Mapped[list[Any] | None] = mapped_column(JSONB, nullable=True)
    ats_recommendation: Mapped[str | None] = mapped_column(Text, nullable=True)
    ats_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, default="new", nullable=False)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )


# ---------------------------------------------------------------------------
# MCQ exam portal (migration e6f7a8b9c0d1 — HR workflow Phase 2)
#
# Relationships are intentionally OMITTED: the routers query explicitly with
# select()/where() (as hr_applicants does) and DB-level ON DELETE CASCADE handles
# child cleanup — this avoids any mapper-config ambiguity from the composite FKs.
# Timestamps are set by the application (repo convention); the migration carries
# matching server_defaults as a raw-SQL safety net.
# ---------------------------------------------------------------------------


class Exam(Base):
    """An MCQ exam authored by an HR manager, scoped to one company.

    status: 'draft' | 'published' | 'closed' (only 'published' is takeable).
    pass_threshold: percent 0-100; passed = score_percent >= pass_threshold.
    time_limit_seconds: optional overall budget; NULL = untimed. Enforced
        SERVER-SIDE at submit (started_at + time_limit_seconds + grace).
    allow_retake: when True an applicant may have >1 submitted attempt (gated
        in-app; attempt_no is server-computed, never client-supplied).
    """

    __tablename__ = "exams"
    __table_args__ = (
        UniqueConstraint("id", "company_id", name="uq_exams_id_company"),
        CheckConstraint(
            "pass_threshold BETWEEN 0 AND 100", name="ck_exams_pass_threshold_range"
        ),
        CheckConstraint("status IN ('draft','published','closed')", name="ck_exams_status"),
        CheckConstraint("kind IN ('mcq','coding')", name="ck_exams_kind"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    target_job_title: Mapped[str | None] = mapped_column(Text, nullable=True)
    pass_threshold: Mapped[int] = mapped_column(SmallInteger, default=60, nullable=False)
    time_limit_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    allow_retake: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    status: Mapped[str] = mapped_column(Text, default="draft", nullable=False)
    # kind: 'mcq' (default) | 'coding'. LEGACY single-round discriminator; the real
    # type now lives on exam_sections.kind. Kept for back-compat (existing flat exams
    # + the default-section migration carry it forward) and as the default for new
    # sections created without an explicit kind.
    kind: Mapped[str] = mapped_column(Text, default="mcq", nullable=False)
    # When True, passing the terminal round (advances_to_interview) auto-creates a
    # scheduled interview invite + emails the candidate. When False, HR invites
    # manually (the eligible-applicants gate). Per-exam, HR-configurable.
    auto_advance_on_pass: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)


class ExamRound(Base):
    """An ordered round within an exam (Aptitude, Coding, Core CS, ...).

    Rounds are the unit HR SCHEDULES and ASSIGNS independently: each round mints its
    own magic link + deadline (exam_assignments.round_id), and a candidate's attempt
    is per-round (exam_attempts.round_id). A round groups one or more exam_sections,
    each of which carries its own kind (mcq | coding) — so a single exam can mix
    MCQ and coding across (and within) rounds.

    pass_threshold/time_limit_seconds default from the exam but are per-round here.
    advances_to_interview marks the terminal round whose pass can auto-advance the
    candidate to an interview (gated by exam.auto_advance_on_pass).
    company_id is pinned to the exam by a composite FK so it cannot drift cross-tenant.
    """

    __tablename__ = "exam_rounds"
    __table_args__ = (
        ForeignKeyConstraint(
            ["exam_id", "company_id"], ["exams.id", "exams.company_id"],
            name="fk_exam_rounds_exam", ondelete="CASCADE",
        ),
        UniqueConstraint("id", "company_id", name="uq_exam_rounds_id_company"),
        CheckConstraint(
            "pass_threshold BETWEEN 0 AND 100", name="ck_exam_rounds_pass_threshold_range"
        ),
        CheckConstraint("status IN ('draft','published')", name="ck_exam_rounds_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    exam_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    company_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    round_number: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    pass_threshold: Mapped[int] = mapped_column(SmallInteger, default=60, nullable=False)
    time_limit_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    advances_to_interview: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    status: Mapped[str] = mapped_column(Text, default="draft", nullable=False)
    position: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)


class ExamSection(Base):
    """A typed section within a round. kind ('mcq' | 'coding') selects which child
    question table + grader the section uses; mixing sections of different kinds in
    one round is how an exam mixes MCQ + coding. company_id is pinned to the round
    by a composite FK. exam_id is denormalized for fast "all sections of an exam".
    """

    __tablename__ = "exam_sections"
    __table_args__ = (
        ForeignKeyConstraint(
            ["round_id", "company_id"], ["exam_rounds.id", "exam_rounds.company_id"],
            name="fk_exam_sections_round", ondelete="CASCADE",
        ),
        UniqueConstraint("id", "company_id", name="uq_exam_sections_id_company"),
        CheckConstraint("kind IN ('mcq','coding')", name="ck_exam_sections_kind"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    round_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    exam_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    company_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    # kind: 'mcq' | 'coding' — selects the question table + grader for this section.
    kind: Mapped[str] = mapped_column(Text, default="mcq", nullable=False)
    time_limit_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    position: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)


class ExamQuestion(Base):
    """An ordered MCQ. correct_index is NEVER serialized to an applicant — the
    take endpoint selects only id/prompt/options/points/position; grading reads
    correct_index server-side only. company_id is pinned to the exam by a
    composite FK so it cannot drift cross-tenant.
    """

    __tablename__ = "exam_questions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["exam_id", "company_id"], ["exams.id", "exams.company_id"],
            name="fk_exam_questions_exam", ondelete="CASCADE",
        ),
        # New parent: the section. company_id is pinned to the section too.
        ForeignKeyConstraint(
            ["section_id", "company_id"], ["exam_sections.id", "exam_sections.company_id"],
            name="fk_exam_questions_section", ondelete="CASCADE",
        ),
        CheckConstraint(
            "jsonb_array_length(options) BETWEEN 2 AND 6",
            name="ck_exam_questions_options_count",
        ),
        CheckConstraint("points >= 1", name="ck_exam_questions_points_positive"),
        CheckConstraint(
            "correct_index >= 0 AND correct_index < jsonb_array_length(options)",
            name="ck_exam_questions_correct_index_range",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    exam_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    section_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    company_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    options: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    correct_index: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    points: Mapped[int] = mapped_column(SmallInteger, default=1, nullable=False)
    position: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)


class CodingQuestion(Base):
    """A coding-round question (exams.kind='coding'). Mirrors ExamQuestion.

    SECRECY (same discipline as ExamQuestion.correct_index): ``reference_solution``
    and any hidden test case's ``expected_output`` are NEVER serialized to the
    applicant — the take endpoint serves only prompt/starter_code/allowed_languages
    and the SAMPLE test cases. company_id is pinned to the exam by a composite FK.

    test_cases: JSONB array of {stdin, expected_output, is_sample(bool), weight(int)}.
    allowed_languages: JSONB array of language slugs, e.g. ["python","cpp"].
    """

    __tablename__ = "coding_questions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["exam_id", "company_id"], ["exams.id", "exams.company_id"],
            name="fk_coding_questions_exam", ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["section_id", "company_id"], ["exam_sections.id", "exam_sections.company_id"],
            name="fk_coding_questions_section", ondelete="CASCADE",
        ),
        CheckConstraint("points >= 1", name="ck_coding_questions_points_positive"),
        CheckConstraint(
            "jsonb_array_length(allowed_languages) >= 1",
            name="ck_coding_questions_languages_count",
        ),
        CheckConstraint(
            "jsonb_array_length(test_cases) >= 1", name="ck_coding_questions_test_cases_count"
        ),
        CheckConstraint("time_limit_ms >= 100", name="ck_coding_questions_time_limit"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    exam_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    section_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    company_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    starter_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    reference_solution: Mapped[str | None] = mapped_column(Text, nullable=True)
    allowed_languages: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    test_cases: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    time_limit_ms: Mapped[int] = mapped_column(Integer, default=5000, nullable=False)
    points: Mapped[int] = mapped_column(SmallInteger, default=100, nullable=False)
    position: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)


class ExamAssignment(Base):
    """Pre-assignment of an applicant to an exam, carrying a HASHED magic-link token.

    Applicants are NOT logged-in users; access is via an opaque random token.
    token_hash = hmac_sha256(raw_token, exam_link_secret). The RAW token lives
    ONLY in the shared URL. Every take request re-resolves THIS row by token_hash
    and checks status NOT IN ('revoked','expired') AND expires_at > now() — so
    revocation, single-use (consumed_at) and rotation are real.
    """

    __tablename__ = "exam_assignments"
    __table_args__ = (
        ForeignKeyConstraint(
            ["exam_id", "company_id"], ["exams.id", "exams.company_id"],
            name="fk_exam_assignments_exam", ondelete="CASCADE",
        ),
        # The token now grants ONE round. company_id pinned to the round too.
        ForeignKeyConstraint(
            ["round_id", "company_id"], ["exam_rounds.id", "exam_rounds.company_id"],
            name="fk_exam_assignments_round", ondelete="CASCADE",
        ),
        UniqueConstraint("token_hash", name="uq_exam_assignments_token_hash"),
        UniqueConstraint("id", "company_id", name="uq_exam_assignments_id_company"),
        CheckConstraint(
            "status IN ('invited','started','completed','expired','revoked')",
            name="ck_exam_assignments_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    exam_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    round_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    applicant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("applicants.id", ondelete="CASCADE"), nullable=False
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    token_hash: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    # Optional scheduled start; gates the first /exam/start join-window (mirrors
    # interview_invites.scheduled_at). NULL = redeemable any time before expiry.
    scheduled_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    consumed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(Text, default="invited", nullable=False)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)


class ExamAttempt(Base):
    """An applicant's server-graded attempt.

    answers: {question_id(str): selected_index(int)} (Pydantic-validated, bounded).
    graded_snapshot: {question_id: {correct_index, points}} frozen at submit so
        later question edits never change a graded result or its audit recompute.
    score_raw/score_max: Integer (no overflow). score_percent: 0-100.
    passed: score_percent >= exams.pass_threshold — computed server-side, the
        single source of truth (the UI never recomputes pass/fail).
    attempt_no: server-computed max(attempt_no)+1; never client-supplied.
    status: 'in_progress' | 'submitted' | 'expired'.
    """

    __tablename__ = "exam_attempts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["exam_id", "company_id"], ["exams.id", "exams.company_id"],
            name="fk_exam_attempts_exam", ondelete="CASCADE",
        ),
        # Attempts are now per-ROUND (a candidate has one attempt per round).
        ForeignKeyConstraint(
            ["round_id", "company_id"], ["exam_rounds.id", "exam_rounds.company_id"],
            name="fk_exam_attempts_round", ondelete="CASCADE",
        ),
        UniqueConstraint(
            "round_id", "applicant_id", "attempt_no", name="uq_exam_attempts_round_applicant_no"
        ),
        CheckConstraint(
            "score_percent IS NULL OR (score_percent BETWEEN 0 AND 100)",
            name="ck_exam_attempts_percent_range",
        ),
        CheckConstraint(
            "status IN ('in_progress','submitted','expired')", name="ck_exam_attempts_status"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    exam_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    round_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    applicant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("applicants.id", ondelete="CASCADE"), nullable=False
    )
    assignment_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("exam_assignments.id", ondelete="RESTRICT"), nullable=True
    )
    attempt_no: Mapped[int] = mapped_column(SmallInteger, default=1, nullable=False)
    answers: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    graded_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    score_raw: Mapped[int | None] = mapped_column(Integer, nullable=True)
    score_max: Mapped[int | None] = mapped_column(Integer, nullable=True)
    score_percent: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    passed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    # Proctoring (mirror of sessions.integrity_score/proctoring_summary). NULL =
    # no proctoring data. Rolling score maintained by /exam/integrity-event.
    integrity_score: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    proctoring_summary: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[str] = mapped_column(Text, default="in_progress", nullable=False)
    started_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    submitted_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)


class ExamIntegrityEvent(Base):
    """One flagged proctoring event during a (coding/MCQ) exam attempt — the exam
    analogue of interview ``integrity_events`` (which key off sessions.id). Exams
    have no session, so these key off the attempt instead.

    event_type: fullscreen_exit | tab_blur | copy | paste | ... (instantaneous;
        ended_at NULL) — extend as needed. Detection is client-side; raw input
        never leaves the browser, only these lightweight events. The rolling
        integrity_score + proctoring_summary live on exam_attempts (read path).
    """

    __tablename__ = "exam_integrity_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["attempt_id"], ["exam_attempts.id"],
            name="fk_exam_integrity_events_attempt", ondelete="CASCADE",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    attempt_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    company_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    started_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    event_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))


class InterviewInvite(Base):
    """HR invitation that drops an applicant into the EXISTING avatar interview
    (HR workflow Phase 3, LAZY-PROVISION model).

    At mint time only this opaque-token row exists. On the applicant's FIRST
    redeem we create a 'guest_candidate' users row (password_hash NULL, company_id
    pinned, the applicant's resume_text copied so the worker grounds the prompt),
    a sessions row for that guest, and the applicant's own DPDP consent
    (purpose='interview', consent_type='interview_voice_recording'). token_hash =
    hmac_sha256(raw, interview_link_secret); the raw token lives only in the URL.

    status: invited -> consumed (set on the FIRST start; thereafter re-enterable so the
            candidate can reconnect to the same session — NOT single-use — until a
            scorecard exists or the link expires) -> completed (a scorecard exists for
            the session) | expired | revoked. The join window
            (settings.interview_join_window_minutes) gates only the first start.
    No relationships (explicit select() per repo convention); DB CASCADE handles cleanup.
    """

    __tablename__ = "interview_invites"
    __table_args__ = (
        ForeignKeyConstraint(
            ["applicant_id", "company_id"], ["applicants.id", "applicants.company_id"],
            name="fk_interview_invites_applicant", ondelete="CASCADE",
        ),
        UniqueConstraint("token_hash", name="uq_interview_invites_token_hash"),
        UniqueConstraint("session_id", name="uq_interview_invites_session"),
        UniqueConstraint("id", "company_id", name="uq_interview_invites_id_company"),
        CheckConstraint(
            "status IN ('invited','consumed','completed','expired','revoked')",
            name="ck_interview_invites_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    applicant_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    job_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("jobs.id", ondelete="RESTRICT"), nullable=False
    )
    guest_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("sessions.id", ondelete="SET NULL"), nullable=True
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    token_hash: Mapped[str] = mapped_column(Text, nullable=False)
    language: Mapped[str] = mapped_column(Text, default="en", nullable=False)
    avatar_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    scheduled_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    consumed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(Text, default="invited", nullable=False)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)


class DpdpConsent(Base):
    """DPDP Act 2023 consent ledger entry.

    Maps to the ``dpdp_consent_ledger`` table created by migration 0001.

    consent_type: opaque string identifying what data processing is consented to.
                  For voice recording: "interview_voice_recording".
    granted:      True = consent given; False = consent explicitly denied.
    granted_at:   Timestamp of consent grant (server-set, UTC).
    revoked_at:   NULL while active; set when user withdraws consent.
    purpose:      Human-readable purpose string, e.g. "interview".
    evidence:     JSONB blob — version, ip_hash (sha256), ua_hash (sha256),
                  consented_at_iso. NEVER contains raw PII.
    """

    __tablename__ = "dpdp_consent_ledger"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id", name="fk_dpdp_consent_ledger_user_id", ondelete="CASCADE"),
        nullable=False,
    )
    consent_type: Mapped[str] = mapped_column(Text, nullable=False)
    granted: Mapped[bool] = mapped_column(Boolean, nullable=False)
    granted_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    purpose: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    user: Mapped[User] = relationship("User", back_populates="consents")


# ---------------------------------------------------------------------------
# Interview data model (migration 0418)
# ---------------------------------------------------------------------------


class Job(Base):
    """A job role available for interview practice.

    level: 'entry' | 'mid' | 'senior' — validated at application layer.
    language: BCP-47 code, default 'en'. Day-1 values: en, hi, te.
    nos_codes: NSQF NOS codes linked to this job, e.g. ['SSC/N9001'].
    competencies: JSONB blob with required/nice-to-have skill lists.
    """

    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    # level: entry | mid | senior
    level: Mapped[str] = mapped_column(Text, nullable=False)
    language: Mapped[str] = mapped_column(Text, default="en", nullable=False)
    nos_codes: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list, nullable=False)
    competencies: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    # B-033 — role context for smarter interviewing
    company_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    department: Mapped[str | None] = mapped_column(Text, nullable=True)
    # interview_type: 'screening' | 'technical' | 'hr'
    interview_type: Mapped[str] = mapped_column(String(16), default="screening", nullable=False)
    # B-032 — JD document storage
    jd_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    jd_s3_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Self-serve custom jobs — NULL = public/seeded job; set = user-created practice job.
    # FK to users.id ON DELETE SET NULL (orphan stays as a public job if user is deleted).
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("users.id", name="fk_jobs_created_by_user_id", ondelete="SET NULL"),
        nullable=True,
    )

    sessions: Mapped[list[Session]] = relationship("Session", back_populates="job")


class NosCompetency(Base):
    """NSQF National Occupational Standards competency record.

    nos_code: unique identifier, e.g. 'SSC/N9001'.
    level: NSQF level 1-9.
    embedding: halfvec(3072) for OpenAI text-embedding-3-large cosine search.
               halfvec uses float16 storage; pgvector 0.8 hnsw supports up to
               4000 halfvec dims vs 2000 for full vector on this build.
               Populated by Sprint 4 NOS ingestion pipeline (nos/embedding.py).
               NULL until ingested. ORM does not map this column — use raw
               SQL / text() for similarity queries.
    """

    __tablename__ = "nos_competencies"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    # nos_code: e.g. "SSC/N9001" — UNIQUE enforced by DB
    nos_code: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # level: NSQF level 1-9
    level: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    # embedding stored as opaque column; pgvector handles the type natively.
    # ORM does not map it — access via raw SQL / text() when needed (Sprint 4).
    sector: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))


class Session(Base):
    """An interview session between a user and the AI interviewer.

    status allowed values: 'created' | 'in_progress' | 'completed' |
                           'abandoned' | 'failed'
    Application layer validates via Pydantic; DB stores as Text for flexibility.
    """

    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("jobs.id", ondelete="RESTRICT"), nullable=False
    )
    language: Mapped[str] = mapped_column(Text, default="en", nullable=False)
    # status: created | in_progress | completed | abandoned | failed
    status: Mapped[str] = mapped_column(Text, default="created", nullable=False)
    started_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # "metadata" is a reserved name on SQLAlchemy's DeclarativeBase; map to
    # the DB column "metadata" via the explicit column name argument.
    session_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, default=dict, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    # Soft-delete column added by migration 20260529_0001 (DPDP erasure).
    deleted_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    # presenter_id: D-ID stock presenter ID from the PRESENTERS catalog.
    # NULL / default means the baseline presenter ("presenter_alice").
    # Added by migration 20260530_0003.
    presenter_id: Mapped[str | None] = mapped_column(Text, nullable=True)

    user: Mapped[User] = relationship("User", back_populates="sessions")
    job: Mapped[Job] = relationship("Job", back_populates="sessions")
    turns: Mapped[list[Turn]] = relationship(
        "Turn", back_populates="session", cascade="all, delete-orphan"
    )


class Turn(Base):
    """A single conversational turn within an interview session.

    speaker allowed values: 'interviewer' | 'candidate'
    Application layer validates via Pydantic; DB stores as Text for flexibility.

    audio_s3_key: placeholder for Sprint 3 voice pipeline; NULL until voice
                  is integrated.
    prompt_tokens / completion_tokens: LLM token counts for cost tracking.
    latency_ms: end-to-end turn latency for p95 monitoring.
    """

    __tablename__ = "turns"
    __table_args__ = (
        UniqueConstraint("session_id", "turn_number", name="uq_turns_session_turn_number"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False
    )
    turn_number: Mapped[int] = mapped_column(Integer, nullable=False)
    # speaker: interviewer | candidate
    speaker: Mapped[str] = mapped_column(Text, nullable=False)
    text_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    # audio_s3_key: Sprint 3 placeholder
    audio_s3_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))

    session: Mapped[Session] = relationship("Session", back_populates="turns")


# ---------------------------------------------------------------------------
# Resume version history (migration 20260530_0002)
# ---------------------------------------------------------------------------


class Resume(Base):
    """One uploaded resume version for a user (Area 3, UI redesign v2).

    Each POST /users/me/resume inserts a new row.  The row whose
    ``is_current=True`` is the active resume; users.resume_text and
    users.resume_s3_key mirror it so the B-033 enrichment path keeps working.

    resume_s3_key: versioned key, e.g. ``resumes/{user_id}/{resume_id}.pdf``.
    is_current:    only ONE row per user should have this flag set at a time.
                   Enforced by a partial unique index in the DB migration.
    """

    __tablename__ = "resumes"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id", name="fk_resumes_user_id", ondelete="CASCADE"),
        nullable=False,
    )
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    resume_text: Mapped[str] = mapped_column(Text, nullable=False)
    resume_s3_key: Mapped[str] = mapped_column(Text, nullable=False)
    is_current: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    uploaded_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))

    user: Mapped[User] = relationship("User", back_populates="resumes")


# ---------------------------------------------------------------------------
# DPDP erasure tables (migration 20260529_0001)
# ---------------------------------------------------------------------------


class ErasureRequest(Base):
    """DPDP Act 2023 §17 — right-to-erasure request record.

    status allowed values: 'pending' | 'completed' | 'failed'
    Application layer validates; DB stores as VARCHAR(16).

    scheduled_for: 30 days after request creation (standard DPDP grace period).
    requested_by:  user_id of the admin who submitted the erasure request.
    artifacts:     JSONB blob recording what was erased (populated on completion).
    """

    __tablename__ = "erasure_requests"

    request_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id", name="fk_erasure_requests_user_id", ondelete="RESTRICT"),
        nullable=False,
    )
    requested_by: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # status: pending | completed | failed — validated at application layer
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    scheduled_for: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    artifacts: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))

    user: Mapped[User] = relationship("User")


class AuditLog(Base):
    """Immutable audit trail for admin actions.

    Used by DPDP erasure and any other privileged admin operations.

    actor_type allowed values: 'admin' | 'system' | 'user'
    ip_address: PostgreSQL INET type — stored as Python str at ORM layer.
    """

    __tablename__ = "audit_log"

    event_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    actor_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    resource_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    details: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    # ip_address uses PostgreSQL INET; mapped as String at ORM layer.
    ip_address: Mapped[str | None] = mapped_column(INET, nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    event_ts: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)


# ---------------------------------------------------------------------------
# Scorecards (migration 20260529_0002)
# ---------------------------------------------------------------------------


class Scorecard(Base):
    """End-of-session AI scorecard — one row per completed interview session.

    scores:       JSONB with keys communication, technical, problem_solving,
                  confidence (each int 0-10). Populated by the Gemini scorer.
    composite_score: Weighted average of the four axes (see S5-006 formula).
    strengths:    JSONB array of 3 strength strings.
    improvements: JSONB array of 3 {area, suggestion} objects.
    summary:      2-3 sentence overall verdict, calibrated to experience tier.
    lang:         BCP-47 language code, e.g. 'en', 'hi', 'te'.
    report_pdf_key: S3/R2 key for the PDF scorecard (NULL until generated).
    transcript_key: S3/R2 key for the transcript JSON export (NULL until done).
    scorer_model: Gemini model ID used for scoring, e.g. 'gemini-2.5-flash'.
    scorer_version: scorer logic version tag, e.g. '1.0'.
    """

    __tablename__ = "scorecards"
    __table_args__ = (
        UniqueConstraint("session_id", name="uq_scorecards_session_id"),
    )

    scorecard_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4
    )
    # session_id is intentionally NOT a FK here: scorecards live in
    # feedback_billing while sessions live in interview_core / data_gateway.
    # Cross-service FK enforcement is done at application layer.
    session_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    scores: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    composite_score: Mapped[float | None] = mapped_column(NUMERIC(4, 2), nullable=True)
    strengths: Mapped[list[Any] | None] = mapped_column(JSONB, nullable=True)
    improvements: Mapped[list[Any] | None] = mapped_column(JSONB, nullable=True)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    lang: Mapped[str] = mapped_column(String(8), nullable=False)
    report_pdf_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    transcript_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    scorer_model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    scorer_version: Mapped[str | None] = mapped_column(String(16), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))


# ---------------------------------------------------------------------------
# Notifications (migration 20260624_0001)
# ---------------------------------------------------------------------------


class Notification(Base):
    """In-app notification feed item backing the AppShell header bell.

    Produced by event handlers (HR invite sent, interview completed, welcome on
    registration). read_at NULL = unread; the bell badge counts unread rows.
    kind: welcome | invite_sent | interview_completed | applicant_scored |
          decision | system — opaque string; the UI maps it to an icon/tone.
    """

    __tablename__ = "notifications"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(Text, default="system", nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    link: Mapped[str | None] = mapped_column(Text, nullable=True)
    read_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))


# ---------------------------------------------------------------------------
# Email system (migration 20260629_0002)
# ---------------------------------------------------------------------------


class EmailEvent(Base):
    """Durable transactional-email outbox + delivery log (one row per email).

    Every send goes through this table: enqueue writes a 'queued' row; the
    background worker (app.mailer) claims it, hands it to the SMTP relay, and
    records the outcome. This unifies three concerns the spec requires:

      * reliable delivery — failed sends retry with exponential backoff up to
        ``max_attempts`` (a flaky relay never silently drops an invite / reset link);
      * logging — status ∈ queued | sending | sent | failed | cancelled is the
        audit trail of every email event (sent / failed / pending);
      * non-blocking — requests only INSERT a row; the actual SMTP I/O happens off
        the request path in the worker.

    SECURITY/DPDP: ``body_html`` / ``body_text`` may contain a live magic link, so
    they are NULLED the instant the row is marked 'sent', and the whole row is
    purged after ``email_retention_days`` by the daily retention cron. The hashed
    side of every magic link lives in its own table — this row never persists a
    reusable secret beyond delivery.

    dedupe_key (optional, unique when present) lets a producer make a send
    idempotent (e.g. one welcome email per registration even on a retry).
    """

    __tablename__ = "email_events"
    __table_args__ = (
        UniqueConstraint("dedupe_key", name="uq_email_events_dedupe_key"),
        CheckConstraint(
            "status IN ('queued','sending','sent','failed','cancelled')",
            name="ck_email_events_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    # template key, e.g. 'welcome' | 'password_reset' | 'exam_link' | ...
    template: Mapped[str] = mapped_column(Text, nullable=False)
    to_email: Mapped[str] = mapped_column(Text, nullable=False)
    # The recipient user (NULL for applicants/guests who are not platform users).
    to_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    # Tenant scoping for the platform-owner email-log view (NULL = platform email).
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("companies.id", ondelete="SET NULL"), nullable=True
    )
    lang: Mapped[str] = mapped_column(Text, default="en", nullable=False)
    subject: Mapped[str] = mapped_column(Text, nullable=False)
    # Rendered bodies — NULLED on successful send (may carry a live magic link).
    body_html: Mapped[str | None] = mapped_column(Text, nullable=True)
    body_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, default="queued", nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=6, nullable=False)
    # When the worker may next attempt this row (backoff). NULL = ASAP.
    next_attempt_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Loose provenance link to the originating entity (no FK — cross-kind).
    related_kind: Mapped[str | None] = mapped_column(Text, nullable=True)
    related_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    dedupe_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)


class AuthToken(Base):
    """Single-use, hashed auth token for password reset + email verification.

    Same discipline as exam/interview magic links: the RAW token lives only in the
    emailed URL; we persist HMAC-SHA256(raw, purpose-secret). A DB read can never
    recover a working token. kind selects the purpose + which secret/TTL applies.

    kind: 'password_reset' | 'email_verify'.
    consumed_at: set on first successful use (single-use). A consumed or expired
    token is rejected by the verify path.
    """

    __tablename__ = "auth_tokens"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_auth_tokens_token_hash"),
        CheckConstraint(
            "kind IN ('password_reset','email_verify')", name="ck_auth_tokens_kind"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    token_hash: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
