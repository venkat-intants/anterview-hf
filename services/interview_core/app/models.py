"""SQLAlchemy ORM models for interview_core — read/write access to the
shared interview schema.

DESIGN DECISION — copy vs shared/:
    These models are deliberately duplicated from data_gateway/app/models.py
    rather than extracted to shared/.  Rationale:

    1. Microservice boundary discipline: each service owns its DB access.
       Coupling two services through shared ORM classes means a column rename
       in data_gateway silently breaks interview_core at import time, not at
       the migration step where the error belongs.

    2. interview_core uses only Job (read-only), Session, and Turn.  It does
       NOT need User, Role, UserRole, NosCompetency — pulling all of those
       into shared/ adds dead weight and forces both services to stay in lock-
       step on unrelated table changes.

    3. The duplication surface is small (< 80 lines of stable DDL-level
       column definitions).  When the schema changes an Alembic migration
       already enforces the update — the ORM classes are secondary
       documentation of that migration, not the source of truth.

    Revisit this decision if a third service also needs Session/Turn access
    (Sprint 4 feedback_billing is a candidate).  At that point shared/ wins.

Covers tables created by data_gateway migrations:
  20260527_0418 — jobs, nos_competencies, sessions, turns
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    TIMESTAMP,
    Boolean,
    ForeignKey,
    Integer,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# User — read-only from interview_core's perspective. Only the fields the
# worker needs are mapped; the full User table is owned by data_gateway.
# ---------------------------------------------------------------------------


class User(Base):
    """A platform user (candidate). Mapped read-only here for resume lookup.

    resume_text holds the extracted text of the candidate's current resume
    (synced by data_gateway on upload), or NULL if none is on file. Used to
    ground interview questions in the candidate's real experience.
    """

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    resume_text: Mapped[str | None] = mapped_column(Text, nullable=True)


# ---------------------------------------------------------------------------
# Job — read-only from interview_core's perspective.
# ---------------------------------------------------------------------------


class Job(Base):
    """A job role available for interview practice.

    level: 'entry' | 'mid' | 'senior' — validated at application layer.
    language: BCP-47 code, default 'en'. Day-1 values: en, hi, te.
    """

    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    level: Mapped[str] = mapped_column(Text, nullable=False)
    language: Mapped[str] = mapped_column(Text, default="en", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))

    sessions: Mapped[list[Session]] = relationship("Session", back_populates="job")


# ---------------------------------------------------------------------------
# Session — created by POST /api/sessions, mutated throughout WS lifecycle.
# ---------------------------------------------------------------------------


class Session(Base):
    """An interview session between a user and the AI interviewer.

    status allowed values: 'created' | 'in_progress' | 'completed' |
                           'abandoned' | 'failed'
    Application layer validates via Pydantic; DB stores as Text for flexibility.
    """

    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    # user_id: DB-level FK to users.id exists in the schema (data_gateway migration).
    # We declare it as a plain column here because interview_core does not map the
    # User table — the DB constraint still enforces referential integrity.
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    job_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("jobs.id", ondelete="RESTRICT"), nullable=False
    )
    language: Mapped[str] = mapped_column(Text, default="en", nullable=False)
    # status: created | in_progress | completed | abandoned | failed
    status: Mapped[str] = mapped_column(Text, default="created", nullable=False)
    # started_at: NOT NULL in DB with server_default=now(). Set explicitly on
    # create to avoid relying on server_default (asyncpg does not pass RETURNING).
    started_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    session_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, default=dict, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    # presenter_id: D-ID stock presenter ID from the PRESENTERS catalog.
    # NULL means the baseline presenter ("presenter_alice").
    # Added by data_gateway migration 20260530_0003.
    presenter_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Phase B proctoring (data_gateway migration 20260618_0002). NULL = no
    # proctoring data (proctoring off / legacy session).
    integrity_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    proctoring_summary: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True
    )

    job: Mapped[Job] = relationship("Job", back_populates="sessions")
    turns: Mapped[list[Turn]] = relationship(
        "Turn", back_populates="session", cascade="all, delete-orphan"
    )


# ---------------------------------------------------------------------------
# Turn — one conversational utterance within a session.
# ---------------------------------------------------------------------------


class Turn(Base):
    """A single conversational turn within an interview session.

    speaker allowed values: 'interviewer' | 'candidate'
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
    audio_s3_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))

    session: Mapped[Session] = relationship("Session", back_populates="turns")


# ---------------------------------------------------------------------------
# IntegrityEvent — one flagged proctoring event within a session (Phase B).
# ---------------------------------------------------------------------------


class IntegrityEvent(Base):
    """A single flagged integrity/proctoring event during an interview.

    event_type: gaze_away | face_absent | multiple_faces | tab_blur |
                fullscreen_exit | copy | paste | second_voice | ...
    ended_at:   NULL for instantaneous events; set for ranged events so a
                duration (and thus a per-second penalty) can be computed.
    event_metadata: optional detail, e.g. {"confidence": 0.7, "yaw_deg": 35}.
    """

    __tablename__ = "integrity_events"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    started_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    event_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
