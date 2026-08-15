"""Real-time interview LiveKit worker — the AVATAR interview engine.

This is the PROVEN path (verified 2026-05-31: Simli publishes avatar_video +
avatar_audio via the LiveKit server API). The product is an avatar interview
([[feedback_avatar_only_not_voice_first]]) — this worker is that product.

Pipeline (official LiveKit Agents pattern):
    candidate mic --LiveKit--> silero VAD + Sarvam STT --> Groq LLM (interviewer)
        --> Sarvam TTS (bulbul:v3, one bound voice) --> avatar (lip-synced
        video+audio published into the room) --> candidate sees + hears

Avatar provider is selected by ``settings.avatar_provider``:
    "simli"  — Simli real-time avatar (default demo avatar)
    "tavus"  — Tavus real-time avatar (demo-only, US-hosted, no India residency;
               persona must be in echo/livekit mode — see scripts/tavus_setup.py)
    "none"   — No avatar; voice-only (safe fallback / CI)

Per-session config arrives in the LiveKit JOB METADATA (set at dispatch time by
the token/launch endpoint): JSON {"session_id","job_title","language","voice"}.
The worker looks nothing up if metadata is absent — it falls back to safe
defaults so a bare dispatch still runs.

CRITICAL ORDERING (from the official example + our proof): call
``avatar.start(session, room)`` BEFORE ``session.start(agent, room)``. Reversing
it = avatar never publishes video. This ordering is enforced for ALL providers.

Run:  poetry run python -m app.worker.interview_worker dev
Prod: poetry run python -m app.worker.interview_worker start

Module map (IC-4). This file kept the parts that are genuinely about running a
LiveKit job — ``InterviewJob``, the checkpoint/recovery layer, the avatar
plumbing, ``entrypoint``/``run`` — and four sibling modules took the parts that
are not:

    app/worker/constants.py      how many questions, how long, how often
    app/worker/prompt.py         the interviewer system instructions
    app/worker/consent.py        DPDP §11 resolver + mid-session watchdog
    app/worker/session_store.py  sessions/turns writes from the worker process

All four are imported back into this namespace, so nothing that referred to
``app.worker.interview_worker.<name>`` — including every ``mock.patch`` target
in the test suite — had to change, and ``python -m app.worker.interview_worker``
is still the entrypoint every deployment runs.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import uuid as _uuid_mod
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import structlog
from livekit import api as lk_api
from livekit import rtc
from livekit.agents import Agent, AgentSession, JobContext, JobProcess, WorkerOptions, cli
from livekit.agents.llm.chat_context import ChatMessage as _ChatMessage
from livekit.agents.voice.events import ConversationItemAddedEvent
from livekit.agents.voice.room_io import RoomOptions
from livekit.plugins import openai, sarvam, silero, simli
from shared.auth.jwt import SERVICE_TOKEN_TTL_SECONDS, issue_access_token

# livekit-plugins-tavus is an optional dependency: the worker must still load
# and run under Simli even when the tavus package is absent.  The module-level
# import is attempted here so IDEs and mypy can resolve the symbol; the runtime
# guard in _build_avatar() means a missing package only fails when
# avatar_provider="tavus" is actually selected.
try:
    from livekit.plugins import tavus as _tavus_plugin
    _TAVUS_AVAILABLE = True
except ImportError:  # pragma: no cover — only absent in stripped envs
    _tavus_plugin = None  # type: ignore[assignment]
    _TAVUS_AVAILABLE = False

# _ParticipantAudioOutput is the exact output RoomIO publishes TTS with in
# voice-only mode. It is a PRIVATE module of livekit-agents (pinned 1.5.15 in
# requirements.txt) — we need it directly for the MID-SESSION voice-only
# fallback: when the avatar participant dies mid-interview, RoomIO's own audio
# output was never created (the avatar owned the audio path), so we build the
# replacement ourselves.
#
# Guarded like the tavus plugin above, and for a stronger reason: an unguarded
# import of a private symbol turns any livekit-agents upgrade that renames or
# moves it into a worker that will not START — i.e. every interview fails,
# including the ones that would never need the fallback. Guarded, the same
# upgrade costs only the mid-session degrade path, which is already a
# best-effort branch. The capability flag is what
# _degrade_to_voice_only_midsession checks so the loss is logged loudly rather
# than surfacing as a bare TypeError from calling None.
try:
    from livekit.agents.voice.room_io._output import _ParticipantAudioOutput
    _PARTICIPANT_AUDIO_OUTPUT_AVAILABLE = True
except ImportError:  # pragma: no cover — only on a livekit-agents upgrade
    _ParticipantAudioOutput = None  # type: ignore[assignment,misc]
    _PARTICIPANT_AUDIO_OUTPUT_AVAILABLE = False

from shared.agents.guardrails import detect_injection
from shared.intelligence import (
    InMemoryProfileCache,
    RoleProfile,
    derive_role_profile,
)
from shared.observability.pii import redact_pii_processor
from shared.redis_factory import build_redis_client

from app.avatars import resolve_avatar
from app.config import settings

# IC-4: four sibling modules carved out of this file (constants, prompt,
# consent, session_store). Imported back here — rather than left for callers to
# find — so every existing import, every
# ``patch("app.worker.interview_worker.<name>")`` target and every caller that
# resolves these as module globals keeps hitting the SAME object, and so
# ``python -m app.worker.interview_worker`` remains the deployment entrypoint it
# has always been. Names still USED below are plain imports; the four kept
# purely for callers pinned to this module path use the ``import X as X`` form,
# which is the explicit re-export idiom (and is what tells the linter they are
# not dead).
from app.worker.consent import (
    _CONSENT_RESOLVE_DB_ERROR as _CONSENT_RESOLVE_DB_ERROR,
)
from app.worker.consent import (
    _RESOLVE_CONSENT_BACKOFF_SECONDS as _RESOLVE_CONSENT_BACKOFF_SECONDS,
)
from app.worker.consent import (
    _RESOLVE_CONSENT_MAX_ATTEMPTS as _RESOLVE_CONSENT_MAX_ATTEMPTS,
)
from app.worker.consent import (
    _lookup_candidate_user_id as _lookup_candidate_user_id,
)
from app.worker.consent import (
    _run_consent_watchdog,
    resolve_consent_user_id,
)
from app.worker.constants import (
    MAX_CANDIDATE_ANSWERS,
    MIN_ANSWERS_TO_SCORE,
    SESSION_WALL_CLOCK_CAP_SECONDS,
)
from app.worker.prompt import (
    _RESUME_PROMPT_CHAR_CAP as _RESUME_PROMPT_CHAR_CAP,
)
from app.worker.prompt import _interviewer_instructions
from app.worker.session_store import (
    _persist_injection_markers,
    _persist_turns,
    _read_session_status,
    _update_session_status,
)
from app.worker_capacity import publish_active_jobs

logger = logging.getLogger("interview-worker")


# ---------------------------------------------------------------------------
# Logging — bring the worker PROCESS inside the PII redaction chain (DPDP §8)
# ---------------------------------------------------------------------------
# The four FastAPI services install redact_pii_processor in app/main.py. The
# worker is a separate process that never imports app.main, so it ran outside
# that chain entirely — the one process whose whole job is handling interview
# transcripts, resumes and JD text was the one process the net did not cover.
#
# Two halves, because this process logs two ways:
#   1. structlog.configure() covers the shared libraries the worker calls into
#      (shared.intelligence.derive, shared.agents, shared.auth.jwt all log via
#      structlog); unconfigured, structlog renders with its defaults and no
#      redaction at all.
#   2. A ProcessorFormatter handler on the stdlib "interview-worker" logger
#      routes this module's own records through the SAME processor chain.
#
# Honest scope: redaction matches on KEY NAME. It covers structlog kwargs and
# stdlib ``extra={...}`` fields; it cannot reach PII interpolated into a %-style
# message string, which every call in this file uses today. So this is the net
# that makes a future structured log call fail safe — "never put PII in a log
# message" remains the actual rule, exactly as shared/observability/pii.py says.
_worker_logging_configured: bool = False


def _configure_worker_logging() -> None:
    """Install the shared PII redaction chain in this worker process. Idempotent.

    Called from ``run()`` (the supervisor process) and from ``_prewarm()`` (once
    per job process — livekit-agents runs interviews in child processes that do
    not inherit a spawn-mode parent's structlog config, and prewarm is the
    framework's per-job-process init hook).
    """
    global _worker_logging_configured  # noqa: PLW0603 — process-wide, one-shot
    if _worker_logging_configured:
        return
    _worker_logging_configured = True

    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    shared_processors: list[Any] = [
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        # Immediately before the renderer: anything added after this point is
        # not covered.
        redact_pii_processor,
    ]

    structlog.configure(
        processors=[*shared_processors, structlog.processors.JSONRenderer()],
        wrapper_class=structlog.make_filtering_bound_logger(level),
    )

    # Bridge: stdlib records from this module through the same chain.
    # foreign_pre_chain is what runs for records that did NOT originate in
    # structlog — i.e. every logger.info() in this file.
    #
    # ExtraAdder FIRST, and it is what makes the bridge more than decoration:
    # ProcessorFormatter otherwise builds the event dict from the rendered
    # message alone, so ``extra={...}`` fields never enter it — the redactor
    # would have nothing to redact and operational fields would be dropped
    # silently along with the PII. Adding them puts both under the same rule.
    handler = logging.StreamHandler()
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=[structlog.stdlib.ExtraAdder(), *shared_processors],
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                structlog.processors.JSONRenderer(),
            ],
        )
    )
    logger.handlers = [handler]
    logger.setLevel(level)
    # Do not also hand these records to livekit-agents' root handler: that would
    # print every worker line twice, once redacted and once not — which is worse
    # than not redacting at all, because the redacted copy makes it look covered.
    logger.propagate = False

# ---------------------------------------------------------------------------
# Admission control — thread-safe counter of currently running interviews.
# ---------------------------------------------------------------------------
# We track active jobs ourselves (in addition to load_threshold) so request_fnc
# can reject jobs over the ceiling WITHOUT waiting for the OS load average to
# catch up (the default _DefaultLoadCalc is CPU-based; on our Oracle Free Tier
# VM the CPU can look idle even as memory fills with VAD models).
_active_jobs: int = 0


def _active_jobs_increment() -> None:
    """Increment the active-jobs counter (called at entrypoint start)."""
    global _active_jobs  # noqa: PLW0603 — module-level mutable counter is intentional
    _active_jobs += 1


def _active_jobs_decrement() -> None:
    """Decrement the active-jobs counter (called at job shutdown hook)."""
    global _active_jobs  # noqa: PLW0603
    _active_jobs = max(0, _active_jobs - 1)


async def _publish_capacity() -> None:
    """Publish the current active-job count to Redis (best-effort, never raises).

    Called after every admission change (increment and decrement) so the HTTP
    server process can read the counter and reject overloaded candidates with
    a clear HTTP 503 before issuing a LiveKit join token — preventing the silent
    "dead room" failure mode where a candidate joins a room with no interviewer.
    """
    import contextlib

    import redis.asyncio as _aioredis

    with contextlib.suppress(Exception):
        rc = _aioredis.from_url(  # type: ignore[no-untyped-call]
            settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=1,
        )
        try:
            await publish_active_jobs(rc, _active_jobs)
        finally:
            await rc.aclose()


# ---------------------------------------------------------------------------
# Module constants — tune here, not in config (scope is only this worker).
# ---------------------------------------------------------------------------

# Sarvam <lang>-IN codes for the STT/TTS plugins.
_LANG_VENDOR: dict[str, str] = {"en": "en-IN", "hi": "hi-IN", "te": "te-IN"}
_GROQ_BASE_URL = "https://api.groq.com/openai/v1"
_GROQ_MODEL = "llama-3.3-70b-versatile"

# MAX_CANDIDATE_ANSWERS, SESSION_WALL_CLOCK_CAP_SECONDS and MIN_ANSWERS_TO_SCORE
# moved to app/worker/constants.py (IC-4) — the prompt builder and the consent
# watchdog now live in sibling modules and need them too. Imported at the top of
# this file; see constants.py for why they are not Settings fields.

# Mid-session voice-only fallback (avatar participant died while the interview
# was live — e.g. the Tavus free-plan per-conversation duration cap fired).
# 24 kHz mono + SOURCE_MICROPHONE mirror RoomIO's AudioOutputOptions defaults,
# i.e. exactly what a voice-only session would have published from the start.
_FALLBACK_AUDIO_SAMPLE_RATE: int = 24000
# Bound on waiting for the candidate to subscribe to the fallback track — if
# the candidate left too, give up instead of hanging the job teardown.
_FALLBACK_SUBSCRIBE_TIMEOUT_SECONDS: float = 15.0

# Service-to-service JWT TTL — generous but finite; scorer returns immediately.
# ALIASED, not redeclared: a service token's `sub` is a service name, so
# logout_all (which only ever writes auth_epoch:<user-uuid>) cannot revoke it —
# its lifetime IS its containment. A containment window with two definitions
# drifts silently, and both happened to read 60 so no test could tell them
# apart. The alias keeps this name for the existing call sites.
_SERVICE_JWT_TTL_SECONDS: int = SERVICE_TOKEN_TTL_SECONDS
# Scoring HTTP timeout and max retry count.
_SCORE_TIMEOUT_SECONDS: float = 15.0
_SCORE_MAX_RETRIES: int = 1

# ---------------------------------------------------------------------------
# Crash-recovery checkpoint (RT-4)
# ---------------------------------------------------------------------------
# Graceful shutdown is already covered (the drain hook handles tab close, abrupt
# disconnect and SIGTERM). A HARD kill — OOM, SIGKILL, node loss — is not: the
# answer count, the transcript and the close flag lived in process memory only,
# so the session row stayed 'in_progress' forever, the transcript was lost, and
# nothing ever scored it. These keys are the durable copy.
_CHECKPOINT_KEY_PREFIX: str = "interview:checkpoint:"
_CLOSE_GUARD_KEY_PREFIX: str = "interview:closed:"

# Comfortably longer than any session (10 min nominal, SESSION_WALL_CLOCK_CAP_
# SECONDS hard cap) so a checkpoint survives a crash, a worker restart and the
# startup sweep — but finite, so orphaned keys evict themselves without an
# operator having to clean Redis.
SESSION_CHECKPOINT_TTL_SECONDS: int = 2 * 60 * 60  # 2 hours

# Checkpoint writes are triggered from the turn loop (NFR: p95 turn latency
# < 2 s), so they get a far tighter bound than the shared client's own 5 s
# socket timeout plus retry schedule. Losing a checkpoint is recoverable;
# stalling a turn is not.
_CHECKPOINT_TIMEOUT_SECONDS: float = 2.0

# A checkpoint that has not been refreshed for longer than this cannot belong to
# a live interview — the wall-clock cap ends every session at 720 s. The margin
# on top absorbs a slow close path (final scoring call + room delete) so the
# reaper never races a session that is still finishing normally.
CHECKPOINT_STALE_AFTER_SECONDS: int = SESSION_WALL_CLOCK_CAP_SECONDS + 300


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


@dataclass
class SessionContext:
    """Everything the worker needs to know about a session before it starts.

    Was a positional 7-tuple until the role-competency engine needed three more
    job fields (skills, department, interview type) to derive a role model. A
    10-element positional tuple is unreadable and every caller/test asserting
    its arity is a tripwire that fires on any addition, so this became a
    dataclass. Defaults are the same safe fallbacks the tuple used to carry, so
    a missing session/job still yields a usable, generic interview.
    """

    job_title: str = "the role"
    language: str = "en"
    experience_level: str = "entry"  # 'entry' | 'mid' | 'senior'
    jd_text: str = ""
    # Catalog avatar id chosen at session-create time (e.g. "anna"). None means
    # unset/legacy row — resolve_avatar(None) returns the default.
    presenter_id: str | None = None
    # Candidate's extracted resume text ("" if none on file) — grounds the
    # interview in their real experience.
    resume_text: str = ""
    # Hiring company from jobs.company_name ("" if unset). The interviewer
    # speaks on behalf of this company; empty keeps it company-neutral.
    company_name: str = ""
    # --- role-model inputs (intelligence layer) ---
    required_skills: list[str] = field(default_factory=list)
    department: str = ""
    interview_type: str = "screening"  # 'screening' | 'technical' | 'hr'
    # Prompt-injection marker NAMES found in resume_text (AG-07). Marker names
    # only — never the matching text, which is the candidate's PII (DPDP §8).
    # Empty on every normal session; carried here so the job can persist it for
    # the reviewing HR manager instead of leaving it in a log line nobody reads.
    injection_markers: list[str] = field(default_factory=list)


def _extract_required_skills(competencies: Any) -> list[str]:
    """Pull a flat skill list out of the ``jobs.competencies`` JSONB blob.

    The column is a free-form blob whose shape has drifted across seeds and
    the HR job-creation UI: sometimes ``{"required": [...], "nice_to_have":
    [...]}``, sometimes a bare list, occasionally a dict of category -> list.
    All three are handled; anything else yields an empty list, which the role
    engine treats as "no skills supplied" rather than failing.
    """
    if isinstance(competencies, list):
        return [str(s).strip() for s in competencies if str(s).strip()][:40]
    if not isinstance(competencies, dict):
        return []

    skills: list[str] = []
    # Prefer the required/must-have buckets; they are the ones that should
    # actually weight the role model.
    for key in ("required", "required_skills", "must_have", "skills"):
        value = competencies.get(key)
        if isinstance(value, list):
            skills.extend(str(s).strip() for s in value if str(s).strip())
    if not skills:
        for value in competencies.values():
            if isinstance(value, list):
                skills.extend(str(s).strip() for s in value if str(s).strip())
    return skills[:40]


def _scan_resume_for_injection(room_name: str, resume_text: str) -> list[str]:
    """Log any prompt-injection markers found in a candidate's resume. Never raises.

    The resume goes verbatim into ``_interviewer_instructions``, so it is the
    one piece of candidate-authored text in a live interview that reaches the
    model before the candidate has said a word. ``feedback_billing`` already
    scans the same document on the scoring path; the live path had no telemetry
    at all, which meant an attempt was only ever visible after the interview —
    if at all.

    DETECTION ONLY, deliberately: we neither strip nor reject. Stripping would
    silently mangle legitimate CVs ("Managed the team responsible for system
    instructions") and would teach candidates to obfuscate rather than stop.
    Same convention as ``feedback_billing/app/untrusted_input.py`` and the
    copilot path — a warning a human sees, never an automatic rejection.

    Only marker names are logged, never the resume text: markers are our own
    literals, the resume is PII (DPDP §8).
    """
    if not resume_text:
        return []
    try:
        markers = detect_injection(resume_text)
    except Exception as exc:  # noqa: BLE001 — telemetry must never block an interview
        logger.warning(
            "interview-worker: injection scan failed room=%s err=%s",
            room_name, type(exc).__name__,
        )
        return []
    if markers:
        logger.warning(
            "interview-worker.injection_markers room=%s source=resume count=%d markers=%r",
            room_name, len(markers), markers,
        )
    return markers


async def _lookup_session(room_name: str) -> SessionContext:
    """Look up session fields needed by the worker for a given room/session.

    The token endpoint names each LiveKit room after the session_id, so the
    worker can resolve the job + language + avatar straight from the DB — no
    dispatch metadata needed (AUTOMATIC dispatch, the proven path).

    Never raises: any missing row or DB failure returns a default
    ``SessionContext`` so the interview still starts.
    """
    import contextlib

    from sqlalchemy import select

    from app.database import get_session_factory, init_engine
    from app.models import Job, User
    from app.models import Session as InterviewSession

    # Guard: a failed init_engine() must not silently escape — log and return
    # defaults instead of propagating the exception to the avatar start path.
    try:
        with contextlib.suppress(Exception):
            init_engine()  # idempotent: builds once per worker proc, then reuses
        sid = _uuid_mod.UUID(room_name)
    except ValueError:
        return SessionContext()
    try:
        factory = get_session_factory()
        async with factory() as db:
            sess = (
                await db.execute(select(InterviewSession).where(InterviewSession.id == sid))
            ).scalar_one_or_none()
            if sess is None:
                return SessionContext()
            lang = (sess.language or "en").lower()
            language = lang if lang in _LANG_VENDOR else "en"
            presenter_id: str | None = sess.presenter_id  # catalog avatar id or None
            # Candidate's current resume text (best-effort — empty if none on file).
            resume_text = ""
            if sess.user_id is not None:
                user = (
                    await db.execute(select(User).where(User.id == sess.user_id))
                ).scalar_one_or_none()
                if user is not None:
                    resume_text = user.resume_text or ""
            # Scanned here, once, on the single path that reads the resume —
            # every SessionContext return below carries the same text, so one
            # call covers them all (OWASP LLM01 telemetry, detection only).
            # The markers ride on the context so the caller can PERSIST them
            # (AG-07); they are deliberately not written from inside this
            # function, which must stay a pure read of the session row.
            markers = _scan_resume_for_injection(room_name, resume_text)
            job = (
                await db.execute(select(Job).where(Job.id == sess.job_id))
            ).scalar_one_or_none()
            if job is None:
                return SessionContext(
                    language=language,
                    presenter_id=presenter_id,
                    resume_text=resume_text,
                    injection_markers=markers,
                )
            # Job.level is 'entry' | 'mid' | 'senior' — maps directly to ScoreRequest.
            level = job.level if job.level in ("entry", "mid", "senior") else "entry"
            return SessionContext(
                job_title=job.title,
                language=language,
                experience_level=level,
                jd_text=(job.description or ""),
                presenter_id=presenter_id,
                resume_text=resume_text,
                company_name=(job.company_name or ""),
                required_skills=_extract_required_skills(job.competencies),
                department=(job.department or ""),
                interview_type=(job.interview_type or "screening"),
                injection_markers=markers,
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "interview-worker: _lookup_session DB query failed room=%s err=%s",
            room_name, type(exc).__name__,
        )
        return SessionContext()


# ---------------------------------------------------------------------------
# Role model (intelligence layer)
# ---------------------------------------------------------------------------

# One cache per worker PROCESS. The worker runs up to
# worker_max_concurrent_jobs interviews in a single process and campus/company
# drives put dozens of candidates through the SAME job back to back — without
# this, every one of them pays for an identical derivation call. Bounded, so a
# long-lived worker cannot grow unboundedly across distinct roles.
_ROLE_PROFILE_CACHE = InMemoryProfileCache(max_entries=128)

# Derivation needs more output budget than a conversational turn: the JSON
# carries 4-8 competencies each with probes and three anchors. settings
# .gemini_max_tokens (1024) truncates it mid-object, which costs the whole role
# model — so this call overrides the budget explicitly.
_ROLE_PROFILE_MAX_TOKENS: int = 3072
# Bounded so a slow derivation cannot delay the candidate's first question.
# On timeout we fall back to the deterministic taxonomy baseline.
_ROLE_PROFILE_TIMEOUT_SECONDS: float = 12.0


async def _derive_role_profile(ctx: SessionContext) -> RoleProfile:
    """Derive the role model for a session. Never raises.

    Runs before the interview starts. Uses Gemini when a key is configured and
    the deterministic taxonomy baseline otherwise — a worker with no Gemini key
    still gets a role-appropriate interview, it just does not get the
    posting-specific refinement.

    Note this is a *different* provider from the conversational LLM (the live
    turn loop runs on Groq). Derivation is a one-off structured-JSON call where
    Gemini's JSON mode is the better fit, and keeping it off the turn-loop
    provider means a Groq incident cannot also cost us the role model.
    """
    llm_caller = None
    if settings.gemini_api_key:
        from app.llm.base import LLMMessage
        from app.llm.gemini import GeminiAdapter

        adapter = GeminiAdapter(
            api_key=settings.gemini_api_key,
            model=settings.gemini_model,
            max_tokens=_ROLE_PROFILE_MAX_TOKENS,
            base_url=settings.gemini_api_base_url,
            timeout_seconds=_ROLE_PROFILE_TIMEOUT_SECONDS,
        )

        async def llm_caller(system_prompt: str, user_prompt: str) -> str:  # noqa: F811
            response = await adapter.generate(
                system_prompt,
                [LLMMessage.user(user_prompt)],
                max_tokens=_ROLE_PROFILE_MAX_TOKENS,
            )
            return response.text

    profile = await derive_role_profile(
        job_title=ctx.job_title,
        jd_text=ctx.jd_text,
        required_skills=ctx.required_skills,
        department=ctx.department,
        company_name=ctx.company_name,
        experience_level=ctx.experience_level,
        interview_type=ctx.interview_type,
        llm=llm_caller,
        cache=_ROLE_PROFILE_CACHE,
    )
    logger.info(
        "interview-worker.role_profile job_title=%r family=%s source=%s "
        "competencies=%d profile_id=%s",
        ctx.job_title, profile.domain_family, profile.source,
        len(profile.competencies), profile.profile_id,
    )
    return profile


# ---------------------------------------------------------------------------
# Service-to-service JWT
# ---------------------------------------------------------------------------


def _mint_service_jwt() -> str:
    """Issue a short-lived HS256 JWT for internal service-to-service calls.

    Claims are minted to match EXACTLY what feedback_billing's _require_jwt
    dependency validates via shared.auth.jwt.verify_access_token:
      - iss: settings.jwt_issuer  ("intants-data-gateway")
      - aud: settings.jwt_audience ("intants-services")
      - exp: now + shared.auth.jwt.SERVICE_TOKEN_TTL_SECONDS
      - jti: fresh uuid4.hex (required by verify_access_token; empty jti raises)
      - sub: "interview_core" (service identity — no role restriction on scorer)
      - roles: ["service"]
    Algorithm: HS256 (settings.jwt_algorithm), secret: settings.jwt_secret.
    """
    # Delegates to the canonical minter rather than building the claims dict
    # here. A second implementation of token minting is the same drift risk that
    # produced the missing revocation check in feedback_billing: this function
    # had to stay manually in step with what verify_access_token requires, and
    # nothing enforced that. The TTL is unchanged (60s) — issue_access_token
    # gained a ttl_seconds parameter for exactly this caller, because swapping to
    # its 900s default would have multiplied this credential's lifetime by 15.
    # The number itself now comes from shared.auth.jwt (see the alias above):
    # the same drift argument applies to the constant, not just the minting.
    return issue_access_token(
        user_id="interview_core",
        roles=["service"],
        secret=settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
        issuer=settings.jwt_issuer,
        audience=settings.jwt_audience,
        ttl_seconds=_SERVICE_JWT_TTL_SECONDS,
    )


# ---------------------------------------------------------------------------
# Scoring call
# ---------------------------------------------------------------------------


async def _post_score(
    session_id: str,
    job_title: str,
    experience_level: str,
    language: str,
    jd_text: str,
    transcript: list[dict[str, str]],
    role_profile: RoleProfile | None = None,
) -> None:
    """POST the transcript to feedback_billing /internal/score.

    Best-effort: logs on failure, never raises. One retry on transient errors.
    Timeout: _SCORE_TIMEOUT_SECONDS. No queue, no Celery.

    The httpx.AsyncClient is constructed once outside the retry loop and reused
    across the single allowed retry — avoids creating a new connection pool per
    attempt.
    """
    url = settings.feedback_billing_url.rstrip("/") + "/internal/score"
    payload: dict[str, Any] = {
        "session_id": session_id,
        "job_title": job_title,
        "experience_level": experience_level,
        "language": language,
        "jd_text": jd_text,
        "turns": transcript,
    }
    # Send the SAME role model the interview was conducted against, so the
    # scorer grades on the rubric the questions actually came from. Deriving it
    # independently in feedback_billing would risk a different profile (cache
    # state, LLM nondeterminism) scoring an interview it did not shape.
    if role_profile is not None:
        payload["role_profile"] = role_profile.model_dump(mode="json")

    # JWT mint is outside the loop — a mint failure aborts immediately without
    # retry and is logged, so we don't thrash on a bad config.
    try:
        token = _mint_service_jwt()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "interview-worker.score.jwt_mint_failed session_id=%s err=%s",
            session_id, type(exc).__name__,
        )
        return

    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    # Single client reused across all retry attempts.
    async with httpx.AsyncClient(timeout=_SCORE_TIMEOUT_SECONDS) as client:
        for attempt in range(_SCORE_MAX_RETRIES + 1):
            try:
                resp = await client.post(url, json=payload, headers=headers)
                if resp.status_code == 409:
                    # Duplicate — idempotency key already scored; not an error.
                    logger.info(
                        "interview-worker.score.duplicate session_id=%s", session_id
                    )
                    return
                resp.raise_for_status()
                data = resp.json()
                logger.info(
                    "interview-worker.score.ok session_id=%s scorecard_id=%s composite=%.2f",
                    session_id, data.get("scorecard_id"), data.get("composite_score"),
                )
                return
            except httpx.HTTPStatusError as exc:
                logger.warning(
                    "interview-worker.score.http_error attempt=%d session_id=%s status=%d",
                    attempt, session_id, exc.response.status_code,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "interview-worker.score.error attempt=%d session_id=%s err=%s",
                    attempt, session_id, type(exc).__name__,
                )
            if attempt < _SCORE_MAX_RETRIES:
                await asyncio.sleep(2.0)

    logger.error(
        "interview-worker.score.failed session_id=%s all %d attempts exhausted",
        session_id, _SCORE_MAX_RETRIES + 1,
    )


# ---------------------------------------------------------------------------
# Closing messages
# ---------------------------------------------------------------------------

_CLOSING_MSG: dict[str, str] = {
    "en": (
        "Thank you so much for your time today. It was a pleasure speaking with you. "
        "We will be in touch soon with the next steps. Take care!"
    ),
    "hi": (
        "आज आपके साथ बात करके बहुत अच्छा लगा। आपके समय का बहुत धन्यवाद। "
        "हम जल्द ही आगे के steps के बारे में आपसे संपर्क करेंगे। ख्याल रखिए!"
    ),
    "te": (
        "ఈరోజు మీతో మాట్లాడటం చాలా ఆనందంగా ఉంది. మీ సమయానికి చాలా ధన్యవాదాలు. "
        "తదుపరి steps గురించి మేము త్వరలో మీకు తెలియజేస్తాము. జాగ్రత్తగా ఉండండి!"
    ),
}
_TIMEOUT_MSG: dict[str, str] = {
    "en": (
        "We have reached the end of our time together. Thank you for the wonderful "
        "conversation — we will be in touch soon!"
    ),
    "hi": (
        "हमारा समय समाप्त हो गया है। इस शानदार conversation के लिए बहुत धन्यवाद — "
        "हम जल्द ही आपसे संपर्क करेंगे!"
    ),
    "te": (
        "మన సమయం అయిపోయింది. ఈ అద్భుతమైన conversation కు చాలా ధన్యవాదాలు — "
        "మేము త్వరలో మీకు తెలియజేస్తాము!"
    ),
}


def _get_closing_msg(language: str, *, timed_out: bool = False) -> str:
    mapping = _TIMEOUT_MSG if timed_out else _CLOSING_MSG
    return mapping.get(language, mapping["en"])


# ---------------------------------------------------------------------------
# Pure interview-state logic — extracted for testability (H-3/H-4/H-6/H-7/H-8)
# ---------------------------------------------------------------------------


class InterviewState:
    """Tracks per-session answer count, transcript, and the single-fire close guard.

    All mutations occur inside the asyncio event loop thread (LiveKit Agents is
    single-threaded per entrypoint), so no locking is required.

    This class is intentionally free of LiveKit imports so it can be unit-tested
    without a running agent session.
    """

    def __init__(self) -> None:
        self.candidate_answer_count: int = 0
        self.transcript: list[dict[str, str]] = []
        self._close_triggered: bool = False

    @property
    def close_triggered(self) -> bool:
        return self._close_triggered

    def mark_close_triggered(self) -> None:
        self._close_triggered = True

    def final_status(self) -> str:
        """Return 'completed' if enough answers, else 'abandoned'."""
        return "completed" if self.candidate_answer_count >= MIN_ANSWERS_TO_SCORE else "abandoned"

    def should_score(self) -> bool:
        """True when we have enough answers to warrant a scoring call."""
        return self.candidate_answer_count >= MIN_ANSWERS_TO_SCORE

    def handle_conversation_item(
        self,
        item: object,
        *,
        on_max_answers: asyncio.Future[None] | None = None,
    ) -> bool:
        """Process one ConversationItemAddedEvent item.

        Returns True if this item was a user answer that pushed the count to
        MAX_CANDIDATE_ANSWERS and close should be scheduled (caller's
        responsibility to call ``_on_close``).

        Mutates: transcript, candidate_answer_count.
        Does NOT mutate _close_triggered (that is the caller's job after
        scheduling the close task, so the guard check stays in one place).
        """
        if not isinstance(item, _ChatMessage):
            return False

        role: str = item.role
        text: str = (item.text_content or "").strip()

        if role not in ("user", "assistant"):
            return False
        if not text:
            return False

        score_role = "user" if role == "user" else "ai"
        # An ANSWER is an exchange, not an utterance. Silero VAD + STT commit a
        # new user item at every pause, so one spoken answer often arrives as
        # several consecutive user items; counting each fragment burned through
        # MAX_CANDIDATE_ANSWERS in ~4 minutes and ended interviews with an
        # abrupt goodbye. Only count a user item when the interviewer has
        # spoken since the candidate's previous item — consecutive fragments
        # collapse into the answer already counted (they still all land in the
        # transcript for scoring).
        prev_role: str | None = self.transcript[-1]["role"] if self.transcript else None
        self.transcript.append({"role": score_role, "text": text})

        if role == "user" and prev_role != "user":
            self.candidate_answer_count += 1
            if (
                self.candidate_answer_count >= MAX_CANDIDATE_ANSWERS
                and not self._close_triggered
            ):
                return True  # signal: caller should schedule close

        return False

    # -- crash-recovery serialisation (RT-4) --------------------------------

    def to_checkpoint(self) -> dict[str, Any]:
        """Return the crash-recoverable slice of this state as plain JSON data.

        Deliberately NOT the whole object: only the three fields that cannot be
        rebuilt from anywhere else after a hard kill. Everything else the worker
        needs on resume (job, language, avatar, role model) is re-derived from
        the DB by ``_lookup_session``, so duplicating it here would just create
        a second source of truth that can go stale.
        """
        return {
            "candidate_answer_count": self.candidate_answer_count,
            "transcript": list(self.transcript),
            "close_triggered": self._close_triggered,
        }

    def restore_checkpoint(self, checkpoint: dict[str, Any]) -> None:
        """Load a previously saved checkpoint over this state. Never raises.

        Every field is validated rather than trusted. A checkpoint is read back
        from Redis, possibly written by an older build of this worker, and this
        runs on the path that starts a candidate's interview — a malformed value
        must degrade to "resume what we could parse", never to an exception that
        costs the candidate their session.
        """
        count = checkpoint.get("candidate_answer_count")
        if isinstance(count, int) and count >= 0:
            self.candidate_answer_count = count

        raw_transcript = checkpoint.get("transcript")
        if isinstance(raw_transcript, list):
            self.transcript = [
                {"role": str(item["role"]), "text": str(item["text"])}
                for item in raw_transcript
                if isinstance(item, dict) and "role" in item and "text" in item
            ]

        if checkpoint.get("close_triggered") is True:
            self._close_triggered = True


# ---------------------------------------------------------------------------
# Durable session checkpoint — survives a hard worker kill (RT-4)
# ---------------------------------------------------------------------------
#
# All four functions below are BEST-EFFORT in the same sense as _persist_turns:
# a Redis outage must never crash, stall or alter an interview. Checkpointing is
# a recovery aid, not a precondition — an interview that runs with Redis down is
# exactly the interview we have always had.


def _checkpoint_key(session_id: str) -> str:
    return _CHECKPOINT_KEY_PREFIX + session_id


def _close_guard_key(session_id: str) -> str:
    return _CLOSE_GUARD_KEY_PREFIX + session_id


# One client per job process, created on first use. The worker never runs the
# FastAPI lifespan, so app.redis_client's singleton is not initialised here and
# get_redis() would raise — this module owns its own. It is built through the
# shared factory so the worker inherits the same serverless-Redis hardening
# (idle health checks, keepalive, bounded timeouts, transport retries) as the
# HTTP services; a hand-rolled pool here is the exact drift shared/redis_factory
# .py exists to stop.
_checkpoint_redis: Any | None = None


def _get_checkpoint_redis() -> Any:
    """Return this process's checkpoint Redis client, building it on first use.

    Lazy rather than built at import: constructing it at import time would make
    every unit test that merely imports the worker carry a connection pool, and
    redis-py pools bind to the event loop that first uses them.
    """
    global _checkpoint_redis  # noqa: PLW0603 — per-process singleton, same as _active_jobs
    if _checkpoint_redis is None:
        _checkpoint_redis = build_redis_client(settings.redis_url)
    return _checkpoint_redis


async def save_checkpoint(
    session_id: str,
    state: InterviewState,
    *,
    started_at: datetime | None = None,
) -> bool:
    """Write the session's recoverable state to Redis. Never raises.

    Returns True when the write landed, False on any failure — the caller uses
    the flag for logging only, never for control flow.

    ``started_at`` is carried so the reaper can record a truthful
    ``duration_seconds`` for a session whose worker never reached the close path.
    """
    payload = {
        "session_id": session_id,
        "updated_at": datetime.now(tz=UTC).isoformat(),
        "started_at": started_at.isoformat() if started_at is not None else None,
        **state.to_checkpoint(),
    }
    try:
        client = _get_checkpoint_redis()
        await asyncio.wait_for(
            client.set(
                _checkpoint_key(session_id),
                json.dumps(payload),
                ex=SESSION_CHECKPOINT_TTL_SECONDS,
            ),
            timeout=_CHECKPOINT_TIMEOUT_SECONDS,
        )
    except Exception as exc:  # noqa: BLE001 — checkpointing must never affect the interview
        logger.warning(
            "interview-worker.checkpoint_write_failed room=%s err=%s",
            session_id, type(exc).__name__,
        )
        return False
    return True


async def load_checkpoint(session_id: str) -> dict[str, Any] | None:
    """Read a session's checkpoint back from Redis, or None. Never raises."""
    try:
        client = _get_checkpoint_redis()
        raw = await asyncio.wait_for(
            client.get(_checkpoint_key(session_id)),
            timeout=_CHECKPOINT_TIMEOUT_SECONDS,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "interview-worker.checkpoint_read_failed room=%s err=%s",
            session_id, type(exc).__name__,
        )
        return None
    return _decode_checkpoint(raw)


def _decode_checkpoint(raw: Any) -> dict[str, Any] | None:
    """Parse a raw checkpoint value into a dict, or None if it is not one."""
    if raw is None:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


async def clear_checkpoint(session_id: str, *, client: Any | None = None) -> None:
    """Delete a session's checkpoint once it has been finalised. Never raises.

    The close-guard key is deliberately NOT deleted with it: the checkpoint says
    "this session may still need recovering", the guard says "this session has
    already been finalised". Dropping the guard here would re-open the door to a
    second scoring run for the rest of the TTL.
    """
    try:
        rc = client if client is not None else _get_checkpoint_redis()
        await asyncio.wait_for(
            rc.delete(_checkpoint_key(session_id)),
            timeout=_CHECKPOINT_TIMEOUT_SECONDS,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "interview-worker.checkpoint_clear_failed room=%s err=%s",
            session_id, type(exc).__name__,
        )


async def claim_close(session_id: str, *, client: Any | None = None) -> bool:
    """Claim the one-and-only finalisation of a session. True if we won it.

    The durable counterpart to ``state.close_triggered``. That flag is a plain
    instance attribute, so it dies with the process: a worker restarted after a
    crash, or the startup reaper sweeping the same session, would run the close
    path — and the scoring call — a second time. SET NX is the whole guard.

    FAILS OPEN (returns True) when Redis cannot answer. The two failure modes
    are not symmetric: a close that is wrongly skipped leaves a candidate with
    no scorecard and a row stuck 'in_progress' forever, while a close that runs
    twice costs at most a duplicate scoring request — which feedback_billing
    already rejects on its own idempotency key (``_post_score`` treats the 409
    as success). Losing the interview is the worse of the two.
    """
    try:
        rc = client if client is not None else _get_checkpoint_redis()
        claimed = await asyncio.wait_for(
            rc.set(
                _close_guard_key(session_id),
                datetime.now(tz=UTC).isoformat(),
                nx=True,
                ex=SESSION_CHECKPOINT_TTL_SECONDS,
            ),
            timeout=_CHECKPOINT_TIMEOUT_SECONDS,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "interview-worker.close_claim_unavailable room=%s err=%s — proceeding",
            session_id, type(exc).__name__,
        )
        return True
    return bool(claimed)


async def restore_state_from_checkpoint(session_id: str) -> InterviewState:
    """Return the InterviewState for a session, resuming a checkpoint if present.

    Called instead of ``InterviewState()`` at the top of every job. With no
    checkpoint this is exactly the old behaviour; with one, the restarted worker
    picks the interview up at the answer it had reached rather than restarting
    the candidate at question one and orphaning the earlier transcript.

    Note the restored ``close_triggered`` is honoured as written. A checkpoint
    that says the close had already started belongs to a session that is being
    finalised elsewhere, and re-running the close from here is precisely what
    ``claim_close`` exists to prevent.
    """
    state = InterviewState()
    checkpoint = await load_checkpoint(session_id)
    if checkpoint is None:
        return state
    state.restore_checkpoint(checkpoint)
    logger.warning(
        "interview-worker.checkpoint_resumed room=%s answers=%d turns=%d closed=%s",
        session_id, state.candidate_answer_count, len(state.transcript),
        state.close_triggered,
    )
    return state


def record_conversation_item(
    item: object,
    *,
    state: InterviewState,
    schedule_checkpoint: Callable[[], None],
) -> bool:
    """Fold one conversation item into *state*, checkpointing if it changed anything.

    Returns the should-close signal from ``InterviewState.handle_conversation_item``
    unchanged; the caller still owns scheduling the close.

    Extracted out of the job object for the same reason
    ``_run_consent_watchdog`` was: the behaviour that matters here — every turn
    the candidate is credited with becomes durable before the process can die —
    is otherwise only reachable through a live LiveKit session, so a dropped
    checkpoint call would break crash recovery with nothing able to notice.
    """
    turns_before = len(state.transcript)
    should_close = state.handle_conversation_item(item)
    # Empty, system and non-chat items are dropped by handle_conversation_item;
    # they changed nothing and are not worth a Redis write.
    if len(state.transcript) != turns_before:
        schedule_checkpoint()
    return should_close


def _checkpoint_age_seconds(checkpoint: dict[str, Any], now: datetime) -> float | None:
    """Seconds since the checkpoint was last refreshed, or None if unknowable."""
    raw = checkpoint.get("updated_at")
    if not isinstance(raw, str):
        return None
    try:
        updated_at = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=UTC)
    return (now - updated_at).total_seconds()


async def reap_stale_sessions(*, now: datetime | None = None) -> list[str]:
    """Finalise sessions whose worker was killed mid-interview. Never raises.

    Runs ONCE per worker process at startup. For every checkpoint that has gone
    stale (no refresh for CHECKPOINT_STALE_AFTER_SECONDS — longer than any
    session can legally run) and whose DB row still says 'in_progress':

      1. claim the close, so a worker that is somehow still alive, or a second
         reaper on another node, cannot finalise it twice;
      2. flush the checkpointed transcript to ``turns`` — the crash is exactly
         the case where the close-path flush never ran, so this is the only copy
         of what the candidate said;
      3. mark the row 'abandoned' with the duration it actually ran.

    Deliberately does NOT score. 'abandoned' is the honest record for an
    interview that was cut off at an unknown point: a scorecard generated from a
    truncated transcript reads as a complete assessment of the candidate, and
    this platform must not manufacture one. The transcript is preserved, so a
    human can re-run scoring against it if they choose.

    Returns the session ids it finalised — for logging and tests, not control
    flow. An empty list is also what a Redis outage returns.
    """
    reaped: list[str] = []
    # Its own client, not the process singleton: this runs on the heartbeat
    # thread's event loop while the singleton belongs to the loop that conducts
    # interviews, and a redis-py pool must not be shared across loops.
    try:
        client = build_redis_client(settings.redis_url)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "interview-worker.reaper_client_failed err=%s", type(exc).__name__
        )
        return reaped

    reference_now = now or datetime.now(tz=UTC)
    try:
        async for key in client.scan_iter(match=_CHECKPOINT_KEY_PREFIX + "*", count=100):
            with contextlib.suppress(Exception):
                await _reap_one(client, key, reference_now, reaped)
    except Exception as exc:  # noqa: BLE001 — a broken sweep must not stop the worker booting
        logger.warning("interview-worker.reaper_failed err=%s", type(exc).__name__)
    finally:
        with contextlib.suppress(Exception):
            await client.aclose()

    if reaped:
        logger.warning(
            "interview-worker.reaper_finalised count=%d sessions=%s",
            len(reaped), ",".join(reaped),
        )
    else:
        logger.info("interview-worker.reaper_clean — no stale sessions found")
    return reaped


async def _reap_one(
    client: Any, key: Any, now: datetime, reaped: list[str]
) -> None:
    """Evaluate and, if warranted, finalise ONE checkpoint. See reap_stale_sessions."""
    key_str = key.decode("utf-8") if isinstance(key, bytes) else str(key)
    checkpoint = _decode_checkpoint(await client.get(key_str))
    if checkpoint is None:
        # Unparseable or already gone — nothing recoverable in it.
        await client.delete(key_str)
        return

    age = _checkpoint_age_seconds(checkpoint, now)
    if age is None or age < CHECKPOINT_STALE_AFTER_SECONDS:
        # Still live (or too damaged to date) — leave it entirely alone. Reaping
        # a running interview would cut off a candidate mid-answer.
        return

    session_id = str(checkpoint.get("session_id") or key_str.rsplit(":", 1)[-1])
    status = await _read_session_status(session_id)
    if status is None:
        # Row missing, or the DB would not answer. Keep the checkpoint so the
        # next sweep can retry rather than losing the transcript to a DB blip.
        return
    if status != "in_progress":
        # Already finalised by the normal close path — the checkpoint is just
        # litter at this point.
        await client.delete(key_str)
        return
    if not await claim_close(session_id, client=client):
        await client.delete(key_str)
        return

    state = InterviewState()
    state.restore_checkpoint(checkpoint)
    await _persist_turns(session_id, state.transcript)

    # The session ended when its worker stopped refreshing the checkpoint, NOT
    # when this sweep happened to notice. Recording the sweep time instead would
    # inflate every crashed session's timings by the whole staleness window and
    # quietly skew the duration analytics the dashboard reports.
    last_seen = now - timedelta(seconds=age)
    started_raw = checkpoint.get("started_at")
    duration: int | None = None
    if isinstance(started_raw, str):
        with contextlib.suppress(ValueError):
            started = datetime.fromisoformat(started_raw)
            if started.tzinfo is None:
                started = started.replace(tzinfo=UTC)
            duration = max(0, int((last_seen - started).total_seconds()))

    logger.warning(
        "interview-worker.reaper_abandoning room=%s age=%.0fs answers=%d turns=%d",
        session_id, age, state.candidate_answer_count, len(state.transcript),
    )
    await _update_session_status(
        session_id,
        "abandoned",
        completed_at=last_seen,
        duration_seconds=duration,
    )
    await client.delete(key_str)
    reaped.append(session_id)


# ---------------------------------------------------------------------------
# Avatar factory — selects provider from settings.avatar_provider
# ---------------------------------------------------------------------------


def _build_avatar(provider: str, replica_id: str | None = None) -> Any:
    """Construct and return an avatar session object for the given provider.

    Args:
        provider:   Value of ``settings.avatar_provider`` ("simli", "tavus",
                    "none", or unknown).
        replica_id: Per-session Tavus replica id resolved from the avatar
                    catalog. Only consumed by the ``tavus`` branch. Falls back
                    to ``settings.tavus_replica_id`` if None (legacy / CI path).
                    Ignored entirely for simli and none providers.

    Returns None when provider is "none" (voice-only mode).

    Raises RuntimeError for missing tavus plugin or missing config, so
    misconfiguration is loud rather than silent.

    SIMLI NOTE: Simli uses its own fixed face (``settings.simli_face_id``).
    The per-session avatar catalog choice does NOT change the Simli face — only
    the Sarvam TTS voice is per-session on the simli path.
    """
    if provider == "simli" or not provider or provider not in ("tavus", "none"):
        # "simli" is the explicit choice; any unrecognised value also falls
        # here to preserve the existing default behaviour.
        if provider not in ("simli", "none", "tavus"):
            logger.warning(
                "interview-worker: unknown avatar_provider=%r; falling back to simli",
                provider,
            )
        return simli.AvatarSession(
            simli_config=simli.SimliConfig(
                api_key=settings.simli_api_key,
                face_id=settings.simli_face_id,
            ),
        )
    if provider == "none":
        return None
    # provider == "tavus"
    if not _TAVUS_AVAILABLE:
        raise RuntimeError(
            "avatar_provider=tavus but livekit-plugins-tavus is not installed. "
            "Run: pip install livekit-plugins-tavus==1.5.15"
        )
    if not settings.tavus_persona_id:
        raise RuntimeError(
            "avatar_provider=tavus requires TAVUS_PERSONA_ID to be set. "
            "Use scripts/tavus_setup.py to create an echo-mode persona and "
            "populate the .env file."
        )
    # Use the per-session replica_id from the catalog if provided; fall back to
    # the settings default so bare/CI dispatches still work.
    effective_replica_id = replica_id or settings.tavus_replica_id
    if not effective_replica_id:
        raise RuntimeError(
            "avatar_provider=tavus: no replica_id resolved (catalog returned None "
            "and TAVUS_REPLICA_ID is not set in .env). Populate TAVUS_REPLICA_ID."
        )
    assert _tavus_plugin is not None  # guarded above by _TAVUS_AVAILABLE check
    # NOTE: do NOT pass api_url here. The tavus plugin's DEFAULT_API_URL already
    # includes the "/v2" path ("https://tavusapi.com/v2") and joins endpoints as
    # f"{api_url}/{endpoint}". Our settings.tavus_api_url is the BARE base
    # ("https://tavusapi.com") used by scripts/tavus_setup.py (which appends
    # "/v2" itself) — passing it here would POST to ".../conversations" (no /v2)
    # and 404. Letting it default keeps the plugin's correct "/v2" base.
    return _tavus_plugin.AvatarSession(
        replica_id=effective_replica_id,
        persona_id=settings.tavus_persona_id,  # shared echo persona — never per-avatar
        api_key=settings.tavus_api_key,
    )


async def _start_avatar_or_fallback(
    avatar: Any,
    session: AgentSession,
    room: Any,
    *,
    provider: str,
    session_id: str,
) -> Any | None:
    """Start the avatar; on ANY failure return None (voice-only), never raise.

    ``avatar.start()`` calls the provider's API (Tavus/Simli create-session).
    A provider outage or exhausted credits (e.g. Tavus HTTP 402) raises there —
    before the avatar reroutes the session's audio output — and previously
    aborted the entrypoint, leaving the candidate alone in a silent dead room.
    Voice-only is always the better failure mode.
    """
    if avatar is None:
        return None
    try:
        await avatar.start(session, room=room)
        logger.info(
            "interview-worker: avatar started provider=%r room=%s",
            provider, session_id,
        )
        return avatar
    except Exception as exc:  # noqa: BLE001 — any provider error degrades, never aborts
        logger.error(
            "interview-worker: avatar start failed provider=%r err_type=%s err=%s — "
            "falling back to voice-only",
            provider, type(exc).__name__, exc,
        )
        # Detach the half-started avatar session (BaseAvatarSession.start
        # registered event handlers + an avatar-join watcher) so it can't
        # touch the AgentSession later. Best-effort.
        with contextlib.suppress(Exception):
            await avatar.aclose()
        return None


async def _degrade_to_voice_only_midsession(
    session: AgentSession,
    room: Any,
    *,
    session_id: str,
) -> bool:
    """Re-route interviewer audio to our own room track after the avatar died.

    In avatar mode ALL interviewer audio flows through the avatar participant
    (echo mode: the provider republishes our TTS with lip-sync). When the
    provider kills the conversation mid-interview — Tavus ends the conversation
    when the plan's duration cap / remaining credits run out — the avatar
    participant leaves the room and the session's DataStreamAudioOutput streams
    into the void: the candidate sits in a silent-but-"Connected" room.

    This swaps ``session.output.audio`` for a directly-published audio track
    (the same output RoomIO uses in voice-only mode). The AgentSession reads
    ``output.audio`` fresh at each speech turn, so the swap takes effect from
    the next utterance.

    Returns True when the swap succeeded (audio will flow again).
    """
    if not _PARTICIPANT_AUDIO_OUTPUT_AVAILABLE:
        # A livekit-agents upgrade moved or renamed the private output class.
        # Say so plainly: without this branch the None below would surface as a
        # bare TypeError inside the generic handler, which reads like a room
        # problem rather than a dependency problem.
        logger.error(
            "interview-worker: voice-only degrade unavailable room=%s — "
            "livekit.agents.voice.room_io._output._ParticipantAudioOutput is "
            "absent (livekit-agents upgrade?); the avatar died and audio cannot "
            "be re-routed",
            session_id,
        )
        return False

    # Abort any speech currently draining into the dead avatar datastream —
    # its playout may never resolve now that the destination is gone.
    with contextlib.suppress(Exception):
        session.interrupt(force=True)

    try:
        output = _ParticipantAudioOutput(
            room,
            sample_rate=_FALLBACK_AUDIO_SAMPLE_RATE,
            num_channels=1,
            track_publish_options=rtc.TrackPublishOptions(
                source=rtc.TrackSource.SOURCE_MICROPHONE
            ),
            track_name="interviewer_audio_fallback",
        )
        # start() publishes the track and waits for the candidate to subscribe.
        await asyncio.wait_for(
            output.start(), timeout=_FALLBACK_SUBSCRIBE_TIMEOUT_SECONDS
        )
    except Exception as exc:  # noqa: BLE001 — fallback must never crash the job
        logger.error(
            "interview-worker: voice-only degrade failed room=%s err_type=%s err=%s",
            session_id, type(exc).__name__, exc,
        )
        return False

    session.output.audio = output
    logger.warning(
        "interview-worker: avatar died mid-session — continuing voice-only room=%s",
        session_id,
    )
    return True


def _install_avatar_death_watch(
    *,
    avatar: Any,
    session: AgentSession,
    room: Any,
    state: InterviewState,
    session_id: str,
) -> None:
    """Continue the interview voice-only if the avatar participant leaves mid-session.

    Registers a ``participant_disconnected`` handler on the room that fires at
    most once, only for the avatar's own identity, and only while the interview
    is still live (normal teardown also removes the avatar participant — the
    ``state.close_triggered`` guard keeps us quiet then).

    After a successful audio swap the interviewer briefly acknowledges the
    glitch and repeats the current question, which both reassures the candidate
    and proves the new audio path end-to-end.
    """
    if avatar is None:
        return
    avatar_identity = getattr(avatar, "avatar_identity", None)
    if not avatar_identity:
        return

    handled = False
    # Strong reference so the recovery task can't be GC'd mid-flight.
    recover_task_holder: dict[str, asyncio.Task[None] | None] = {"task": None}

    async def _recover() -> None:
        ok = await _degrade_to_voice_only_midsession(
            session, room, session_id=session_id
        )
        if not ok or state.close_triggered:
            return
        with contextlib.suppress(Exception):
            await session.generate_reply(
                instructions=(
                    "The video avatar just dropped due to a technical issue, but "
                    "the audio call is still live. In ONE short sentence reassure "
                    "the candidate that the interview continues in audio-only "
                    "mode, then repeat your last question. Do NOT advance to a "
                    "new question."
                )
            )

    def _on_participant_disconnected(participant: Any) -> None:
        nonlocal handled
        if handled or state.close_triggered:
            return
        if getattr(participant, "identity", None) != avatar_identity:
            return
        handled = True
        logger.warning(
            "interview-worker: avatar participant %r disconnected mid-session "
            "room=%s — degrading to voice-only",
            avatar_identity, session_id,
        )
        recover_task_holder["task"] = asyncio.create_task(_recover())

    room.on("participant_disconnected", _on_participant_disconnected)


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


class InterviewJob:
    """One live interview: its state, its LiveKit session, and its task handles.

    This was a 599-line ``entrypoint()`` with nine closures over shared mutable
    locals. The extraction is behaviour-preserving — the statement order in
    ``run()`` below is exactly the order the function body had — but it changes
    two things that were load-bearing and only ever written down in prose:

    **Task lifetime.** asyncio holds only WEAK references to tasks, so a task
    nobody keeps can be collected mid-flight. The old body defended against that
    three times over with ad-hoc holder dicts (``_teardown_task_holder``,
    ``recover_task_holder``, the ``checkpoint_tasks`` set) and missed it once, on
    the NORMAL close path (the task created after the candidate's tenth answer).
    Task handles are now ATTRIBUTES, which are strong references by
    construction: the holder dicts and the bug they were guarding against are the
    same problem, and both are gone.

    **Binding order.** Closures made "which name is bound before which handler is
    registered" a correctness constraint — ``cap_task`` in particular carried a
    comment about a fixed ``UnboundLocalError``. Attributes are initialised to
    ``None`` in ``__init__``, so a handler that fires early now reads ``None``
    and no-ops instead of raising. The ordering comments below are retained
    because the ordering is still deliberate; they are no longer the only thing
    holding it up.

    Interview contract (unchanged):

    AUTOMATIC dispatch: the worker joins every room created (each room == one
    interview). It resolves the job/language from the DB by room name (==
    session_id), so no explicit dispatch or metadata is required.

    Question-count logic (code-enforced, not just prompt):
      - Each ConversationItemAddedEvent with item.role == "user" is one candidate
        answer. We count from 1.
      - After the candidate's MAX_CANDIDATE_ANSWERSth answer: say warm close,
        shutdown session, score transcript.
      - SESSION_WALL_CLOCK_CAP_SECONDS safety cap fires whichever comes first.

    NOTE (duration accuracy): session_started_at is set in _resolve_context,
    BEFORE avatar.start() and session.start(), so elapsed time includes
    cold-start setup time (~1-3s). This is a known minor overcount;
    re-architecting it would require a separate "first candidate audio"
    timestamp which adds complexity for negligible gain.

    ADMISSION CONTROL: increments _active_jobs on entry; decrements via the
    framework's add_shutdown_callback so the counter is always consistent.

    TEARDOWN RELIABILITY: _abrupt_close is wrapped in asyncio.shield() and
    tracked so the framework's shutdown hook can await it.  This prevents the
    task from being GC'd when the candidate closes their browser (the most
    common abrupt exit), which previously left sessions stuck 'in_progress'
    with no scorecard.
    """

    def __init__(self, ctx: JobContext) -> None:
        self.ctx = ctx

        # Bound in _resolve_context, NOT here: rtc.Room.name is empty until
        # ctx.connect() has populated the room info, so reading it in __init__
        # would silently give every job the session_id "".
        self.session_id: str = ""

        # Job metadata — read from the DB in _resolve_context. Defaults match
        # SessionContext's, so a failed lookup still yields a usable interview.
        self.session_ctx: SessionContext = SessionContext()
        self.job_title: str = self.session_ctx.job_title
        self.language: str = self.session_ctx.language
        self.experience_level: str = self.session_ctx.experience_level
        self.jd_text: str = self.session_ctx.jd_text
        self.resume_text: str = self.session_ctx.resume_text
        self.company_name: str = self.session_ctx.company_name

        # Declared, not defaulted: every reader of these runs after the phase
        # that binds them, and giving them a None state would put a guard on
        # each call site to describe a situation that cannot occur.
        self.role_profile: RoleProfile
        self.session: AgentSession[None]
        self.voice: str
        self.avatar_replica_id: str
        self.vendor_lang: str

        self.state: InterviewState = InterviewState()
        self.session_started_at: datetime = datetime.now(tz=UTC)

        # --- task handles ---------------------------------------------------
        # Every one of these is a STRONG reference to a task asyncio itself only
        # references weakly. See the class docstring.
        #
        # cap_task and consent_task are read by _on_session_close, which the
        # framework can fire at any point after the session exists — hence the
        # None initialisation rather than an assume-bound attribute.
        self.cap_task: asyncio.Task[None] | None = None
        self.consent_task: asyncio.Task[None] | None = None
        # The REAL inner teardown task created by _on_session_close (never the
        # asyncio.shield wrapper — see _on_session_close for why).
        self.teardown_task: asyncio.Task[None] | None = None
        # The normal close, scheduled from the conversation-item handler after
        # the candidate's final answer.
        self.close_task: asyncio.Task[None] | None = None
        # In-flight checkpoint writes; a set because several can overlap.
        self.checkpoint_tasks: set[asyncio.Task[bool]] = set()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """Drive one interview from admission to first question.

        THE ORDER OF THESE CALLS IS THE CONTRACT. Each step's docstring says
        what the next one depends on; three are load-bearing enough to name
        here:
          • _resolve_context needs ctx.connect() to have populated room.name;
          • _wire_lifecycle must run before _start_avatar, so a "close" event
            during avatar startup finds its handler and its cap task;
          • _start_avatar must run before _start_interview — avatar.start()
            before session.start(), or the avatar never publishes video.

        The framework keeps the session alive after this returns; teardown runs
        through the "close" listener and the shutdown callbacks registered here.
        """
        await self._admit()
        await self.ctx.connect()
        await self._resolve_context()
        self._build_agent_session()
        self._wire_lifecycle()
        await self._start_avatar()
        await self._start_interview()
        await self._start_consent_watchdog()

    async def _admit(self) -> None:
        """Count this job against the concurrency ceiling and register its release."""
        _active_jobs_increment()
        # Publish the updated count immediately so the HTTP server can reject
        # further requests if we're now at the ceiling.  Best-effort — never raises.
        await _publish_capacity()

        # Register the decrement immediately so it fires on any exit path (normal
        # close, abrupt disconnect, SIGTERM drain, crash).  add_shutdown_callback
        # guarantees this runs even when the entrypoint raises.
        self.ctx.add_shutdown_callback(self._decrement_job_counter)

    async def _decrement_job_counter(self) -> None:
        """Shutdown callback — free this job's slot in the admission counter."""
        _active_jobs_decrement()
        # Publish the decremented count so the HTTP server sees freed capacity.
        await _publish_capacity()

    async def _resolve_context(self) -> None:
        """Resolve session metadata, role model and avatar. Requires a connected room."""
        # DB lookup is best-effort and MUST NEVER crash the avatar path.
        try:
            self.session_ctx = await _lookup_session(self.ctx.room.name)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "interview-worker: session lookup failed, using defaults: %s",
                type(exc).__name__,
            )

        # Attribute aliases — the close/scoring paths below read these names.
        session_ctx = self.session_ctx
        self.job_title = session_ctx.job_title
        self.language = session_ctx.language
        self.experience_level = session_ctx.experience_level
        self.jd_text = session_ctx.jd_text
        presenter_id = session_ctx.presenter_id
        self.resume_text = session_ctx.resume_text
        self.company_name = session_ctx.company_name

        # AG-07: surface any prompt-injection markers found in the resume to the
        # reviewing human, not only to the log stream. No-op (and no round trip)
        # on the normal path where the list is empty.
        await _persist_injection_markers(self.ctx.room.name, session_ctx.injection_markers)

        # Role model — drives question planning below. Never raises; degrades to
        # the deterministic taxonomy baseline.
        self.role_profile = await _derive_role_profile(session_ctx)

        # Resolve the per-session avatar: voice (Sarvam TTS speaker) + replica_id
        # (Tavus face). resolve_avatar() never raises — unknown/None → default "anna".
        # Voice applies to BOTH simli and tavus paths (it's the TTS speaker layer).
        # replica_id is only consumed by the tavus path; simli uses its fixed face.
        resolved = resolve_avatar(presenter_id)
        self.voice = resolved.voice
        self.avatar_replica_id = resolved.replica_id

        self.vendor_lang = _LANG_VENDOR[self.language]
        self.session_id = self.ctx.room.name  # room name == session_id UUID string

        logger.info(
            "interview-worker.start room=%s job_title=%r language=%s voice=%s "
            "avatar_id=%s level=%s resume_chars=%d",
            self.session_id, self.job_title, self.language, self.voice, resolved.id,
            self.experience_level, len(self.resume_text or ""),
        )

        # ------------------------------------------------------------------
        # Per-session state — single InterviewState instance; all mutations happen
        # inside the asyncio event loop thread so no lock is needed.
        #
        # Resumed from Redis when a checkpoint exists, so a worker restarted after a
        # hard kill continues the interview at the answer it had reached instead of
        # starting the candidate over and orphaning the earlier transcript. With no
        # checkpoint (the normal case) this returns a fresh InterviewState.
        # ------------------------------------------------------------------
        self.state = await restore_state_from_checkpoint(self.session_id)
        self.session_started_at = datetime.now(tz=UTC)

    def _build_agent_session(self) -> None:
        """Build the AgentSession (VAD + Sarvam STT/TTS + Groq LLM).

        Uses the prewarmed VAD from prewarm_fnc if available; falls back to
        cold-loading in case prewarm failed.
        """
        ctx = self.ctx
        _prewarmed_vad = getattr(ctx.proc, "userdata", {}).get("vad") if ctx.proc else None
        vad_instance = _prewarmed_vad if _prewarmed_vad is not None else silero.VAD.load()
        if _prewarmed_vad is not None:
            logger.info(
                "interview-worker: using prewarmed silero VAD room=%s", self.session_id
            )
        else:
            logger.info(
                "interview-worker: cold-loading silero VAD (prewarm unavailable) room=%s",
                self.session_id,
            )

        self.session = AgentSession(
            vad=vad_instance,
            stt=sarvam.STT(
                language=self.vendor_lang,
                model=settings.sarvam_stt_model,
                api_key=settings.sarvam_api_key,
            ),
            llm=openai.LLM(
                model=_GROQ_MODEL,
                api_key=settings.groq_api_key,
                base_url=_GROQ_BASE_URL,
            ),
            tts=sarvam.TTS(
                target_language_code=self.vendor_lang,
                model="bulbul:v3",
                speaker=self.voice,
                api_key=settings.sarvam_api_key,
            ),
        )

    def _wire_lifecycle(self) -> None:
        """Register every handler and background task the session close path needs.

        Runs BEFORE avatar.start(): the "close" event can fire during avatar
        startup, and it must find both its handler and a cap task to cancel.
        """
        self.session.on("conversation_item_added", self._on_conversation_item_added)

        # Wall-clock safety cap — created BEFORE registering the "close" event
        # handler and BEFORE avatar.start(), so _on_session_close can always
        # safely cancel it regardless of when the "close" event fires.
        # (The attribute defaults to None, so an out-of-order close now no-ops
        # rather than raising — but the ordering is still the design.)
        self.cap_task = asyncio.create_task(self._wall_clock_cap())

        # "close" event handler — fires on ANY session close (normal or abrupt).
        # This is the hook for post-session DB update and scoring when the
        # candidate disconnects without triggering _on_close (e.g. browser tab
        # closed mid-session). The state._close_triggered guard prevents
        # double-execution.
        self.session.on("close", self._on_session_close)

        # Register a framework-level shutdown hook that awaits the teardown task.
        # This hook fires when the job process is shutting down (SIGTERM / drain
        # timeout) and ensures _abrupt_close always completes even if the LiveKit
        # framework cancels tasks during the drain.
        self.ctx.add_shutdown_callback(self._await_teardown_on_shutdown)

    async def _start_avatar(self) -> None:
        """Start the avatar, or degrade to voice-only. NEVER raises.

        CRITICAL: this must happen before session.start(). Reversing it = the
        avatar never publishes video. Enforced for every provider.
        """
        try:
            # Pass the per-session replica_id so the tavus branch uses the chosen
            # face. Simli and "none" providers ignore replica_id entirely.
            avatar = _build_avatar(
                settings.avatar_provider, replica_id=self.avatar_replica_id
            )
        except RuntimeError as exc:
            # Misconfiguration (missing plugin / persona / replica). Loud, but the
            # interview must still happen — degrade to voice-only.
            logger.error(
                "interview-worker: avatar setup failed provider=%r err=%s — "
                "falling back to voice-only",
                settings.avatar_provider, exc,
            )
            avatar = None

        avatar = await _start_avatar_or_fallback(
            avatar, self.session, self.ctx.room,
            provider=settings.avatar_provider, session_id=self.session_id,
        )
        if avatar is None:
            logger.info(
                "interview-worker: running voice-only room=%s provider=%r",
                self.session_id, settings.avatar_provider,
            )
        else:
            # The provider can also kill the avatar MID-interview (e.g. Tavus ends
            # the conversation when the plan's duration cap or credits run out —
            # observed as "audio dies at ~3 minutes" on the free plan). Watch for
            # the avatar participant leaving and continue the interview voice-only.
            _install_avatar_death_watch(
                avatar=avatar,
                session=self.session,
                room=self.ctx.room,
                state=self.state,
                session_id=self.session_id,
            )

    async def _start_interview(self) -> None:
        """Mark the session live, checkpoint it, start the agent and ask Q1."""
        # Mark session in_progress.
        await _update_session_status(
            self.session_id, "in_progress", started_at=self.session_started_at
        )

        # First checkpoint, written the moment the row can get stuck 'in_progress'.
        # The reaper sweeps CHECKPOINTS, so a session that dies before the
        # candidate's first answer needs one to already exist — without this, a
        # worker killed during avatar startup would leave exactly the kind of
        # permanently-'in_progress' row the reaper was added to clean up.
        await save_checkpoint(
            self.session_id, self.state, started_at=self.session_started_at
        )

        await self.session.start(
            agent=Agent(
                instructions=_interviewer_instructions(
                    self.job_title, self.language, self.resume_text,
                    self.company_name, self.role_profile,
                )
            ),
            room=self.ctx.room,
            # text_input=False is load-bearing, NOT tidiness. livekit-agents
            # defaults it to ENABLED ("if text_input is not given, default to
            # enabled" — room_io/types.py), which registers a handler on the
            # `lk.chat` text stream. Its default callback feeds the text straight
            # into generate_reply() as a user turn, so it lands in the same
            # conversation_item stream we build the transcript from and count
            # answers against.
            #
            # For a VOICE interview platform that is a total assessment bypass: a
            # candidate opens devtools, publishes text on lk.chat instead of
            # speaking, and the answer is scored as theirs — with Sarvam STT, the
            # VAD turn detection, gaze/face proctoring and second-voice detection
            # all sitting on the audio path that was never used. It is also the
            # clean channel for prompt injection, since the text arrives verbatim
            # rather than through STT.
            #
            # This default can flip on a livekit-agents bump — see the regression
            # test in tests/unit/test_worker_reliability.py.
            room_options=RoomOptions(text_input=False),
        )
        # Greet the candidate without waiting — the avatar should speak first on join.
        # This IS Q1 (the self-introduction question). Do NOT ask the candidate to
        # introduce themselves again later — the system prompt already lists Q1 as
        # self-intro, and this greeting fulfils that slot. Ask no other question here.
        await self.session.generate_reply(
            instructions=(
                "This is Q1. Greet the candidate warmly and ask them to briefly introduce "
                "themselves. Do NOT ask any other question in this turn."
            )
        )
        logger.info("interview-worker: session started room=%s", self.session_id)

    async def _start_consent_watchdog(self) -> None:
        """Start the DPDP consent watchdog now that the session is live.

        resolve_consent_user_id covers both registered-candidate and guest
        magic-link sessions (both always set sessions.user_id).
        Returns:
          str uuid   → valid user found; watchdog will poll consent.
          None       → legit no-op (unrecognised/CI room); watchdog skips.
          _CONSENT_RESOLVE_DB_ERROR → transient DB error after retries;
                       watchdog will FAIL-CLOSED (end session) to protect DPDP §11.
        """
        candidate_user_id = await resolve_consent_user_id(self.session_id)
        self.consent_task = asyncio.create_task(
            self._consent_watchdog(candidate_user_id)
        )

    # ------------------------------------------------------------------
    # Close paths — shared close logic fires exactly once regardless of trigger.
    # ------------------------------------------------------------------

    async def _on_close(self, *, timed_out: bool, consent_withdrawn: bool = False) -> None:
        """Warm close: say goodbye, update DB, fire scorer. Best-effort.

        consent_withdrawn=True (DPDP §11 right-to-withdraw): the candidate revoked
        recording consent mid-session. We end IMMEDIATELY — skip the spoken closing
        pleasantry (no further TTS) and DO NOT score, because scoring is fresh
        processing of the recording the candidate just withdrew consent for. The
        transcript captured while consent WAS valid is still persisted for audit,
        and the session is marked 'abandoned'.
        """
        state = self.state
        session_id = self.session_id
        if state.close_triggered:
            return
        state.mark_close_triggered()

        # Record that closing has begun BEFORE anything slow runs — the close
        # path speaks a goodbye, writes the DB and makes an HTTP scoring call,
        # and a hard kill anywhere in that window previously left no durable
        # trace that it had started.
        await save_checkpoint(session_id, state, started_at=self.session_started_at)

        # Durable single-fire guard, checked in addition to the in-memory flag
        # above. close_triggered dies with the process, so a worker restarted
        # after a crash — or the startup reaper sweeping the same session —
        # would write the status and fire the scorer a second time. Losing the
        # claim means someone else already finalised this session: we still tear
        # the room down locally (the candidate must not be left connected to a
        # dead room) but touch nothing durable.
        finalise = await claim_close(session_id)

        # Speak the closing line before shutting the agent down — but NOT on a
        # consent withdrawal, where we stop processing at once.
        if not consent_withdrawn:
            try:
                closing_text = _get_closing_msg(self.language, timed_out=timed_out)
                handle = self.session.say(closing_text, allow_interruptions=False)
                await handle
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "interview-worker: closing say() failed room=%s err=%s",
                    session_id, type(exc).__name__,
                )

        # Shutdown the agent session (clean, drain=True by default).
        try:
            self.session.shutdown()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "interview-worker: session.shutdown() failed room=%s err=%s",
                session_id, type(exc).__name__,
            )

        if not finalise:
            logger.warning(
                "interview-worker.close_already_finalised room=%s — skipping "
                "status write, transcript flush and scoring",
                session_id,
            )
        else:
            # DB: mark session completed/abandoned + timing.
            now = datetime.now(tz=UTC)
            elapsed = int((now - self.session_started_at).total_seconds())
            final_status = "abandoned" if consent_withdrawn else state.final_status()
            await _update_session_status(
                session_id,
                final_status,
                completed_at=now,
                duration_seconds=elapsed,
            )

            # Persist the transcript to the turns table (audit trail + admin
            # drill-in view + re-scoring resilience). Done for every close, even
            # abandoned sessions, so the DB always has whatever was said.
            await _persist_turns(session_id, state.transcript)

            # Score only if we have enough answers AND consent was not withdrawn.
            # We AWAIT it (not fire-and-forget) so the scorecard row exists BEFORE
            # we delete the room below — deleting the room tears this job down and
            # would cancel a background task.
            if consent_withdrawn:
                logger.warning(
                    "interview-worker.consent_withdrawn.closed room=%s answers=%d — scoring skipped",
                    session_id, state.candidate_answer_count,
                )
            elif state.should_score():
                logger.info(
                    "interview-worker.score.firing room=%s answers=%d turns=%d",
                    session_id, state.candidate_answer_count, len(state.transcript),
                )
                await _post_score(
                    session_id=session_id,
                    job_title=self.job_title,
                    experience_level=self.experience_level,
                    language=self.language,
                    jd_text=self.jd_text,
                    transcript=state.transcript,
                    role_profile=self.role_profile,
                )
            else:
                logger.info(
                    "interview-worker.score.skipped room=%s answers=%d < min=%d",
                    session_id, state.candidate_answer_count, MIN_ANSWERS_TO_SCORE,
                )

            # The session is durably finalised — drop the checkpoint so the
            # startup reaper has nothing left to find. The close-guard key stays
            # for its full TTL; that is what keeps this single-fire.
            await clear_checkpoint(session_id)

        # End the call: delete the LiveKit room so the candidate is disconnected
        # and the frontend navigates to the results page. session.shutdown() only
        # removes the agent/avatar participant — without this the candidate stays
        # connected indefinitely (interview "never ends").
        try:
            lkapi = lk_api.LiveKitAPI(
                url=settings.livekit_url,
                api_key=settings.livekit_api_key,
                api_secret=settings.livekit_api_secret,
            )
            try:
                await lkapi.room.delete_room(lk_api.DeleteRoomRequest(room=session_id))
                logger.info("interview-worker.room_deleted room=%s", session_id)
            finally:
                await lkapi.aclose()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "interview-worker: delete_room failed room=%s err=%s",
                session_id, type(exc).__name__,
            )

    async def _abrupt_close(self) -> None:
        """DB update + conditional scoring for unexpected disconnects.

        Launched as a real asyncio.Task by _on_session_close and held in
        self.teardown_task so the framework's shutdown hook can await it.
        asyncio.shield() is applied at await-time (in _await_teardown_on_shutdown),
        not here, ensuring the coroutine body is never GC'd when the candidate
        closes their browser before it finishes.
        """
        state = self.state
        session_id = self.session_id
        if state.close_triggered:
            return
        state.mark_close_triggered()
        # Same durable single-fire guard as _on_close: the in-memory flag cannot
        # survive the process, so it cannot stop a restarted worker or the
        # startup reaper from finalising this session a second time.
        if not await claim_close(session_id):
            logger.warning(
                "interview-worker.abrupt_close_already_finalised room=%s", session_id
            )
            return
        now = datetime.now(tz=UTC)
        elapsed = int((now - self.session_started_at).total_seconds())
        await _update_session_status(
            session_id,
            state.final_status(),
            completed_at=now,
            duration_seconds=elapsed,
        )
        # Persist the transcript before scoring (audit + admin view + resilience).
        await _persist_turns(session_id, state.transcript)
        if state.should_score():
            # Await directly: the candidate already disconnected and this job is
            # tearing down — a bare background task would be cancelled before the
            # scorecard is written.  asyncio.shield() above keeps us alive.
            await _post_score(
                session_id=session_id,
                job_title=self.job_title,
                experience_level=self.experience_level,
                language=self.language,
                jd_text=self.jd_text,
                transcript=state.transcript,
                role_profile=self.role_profile,
            )
        # Durably finalised — the reaper has nothing left to recover here.
        await clear_checkpoint(session_id)

    # ------------------------------------------------------------------
    # Event handlers and background tasks
    # ------------------------------------------------------------------

    def _checkpoint_soon(self) -> None:
        """Schedule a best-effort checkpoint write. Never blocks the turn loop.

        Awaiting the write inline would put a cloud Redis round-trip between the
        candidate finishing a sentence and the interviewer replying, on the very
        path the p95 < 2 s NFR measures. Scheduling it costs at most one answer's
        worth of recovery if the process dies in the gap; awaiting it costs the
        latency budget on every single turn.

        self.checkpoint_tasks is the strong reference: without it a write can be
        garbage-collected mid-flight, which would silently defeat the crash
        recovery it enables.
        """
        task = asyncio.create_task(
            save_checkpoint(
                self.session_id, self.state, started_at=self.session_started_at
            )
        )
        self.checkpoint_tasks.add(task)
        task.add_done_callback(self.checkpoint_tasks.discard)

    def _on_conversation_item_added(self, event: ConversationItemAddedEvent) -> None:
        """Handle every committed conversation item.

        Called by livekit-agents 1.5.x via AgentSession.on("conversation_item_added", ...).
        The event carries a ChatMessage with:
          item.role: "user" (candidate) | "assistant" (agent/interviewer)
          item.text_content: str | None — the committed transcript text
          item.interrupted: bool — True if the agent's speech was cut off mid-sentence

        Only role=="user" items are candidate answers that count toward MAX_CANDIDATE_ANSWERS.
        We include both "user" and "assistant" items in the transcript for scoring, mapping:
          "user"      -> ScoreRequest TurnIn role "user"
          "assistant" -> ScoreRequest TurnIn role "ai"
        We skip "system" / "developer" messages (not present in normal interview flow).
        """
        should_close = record_conversation_item(
            event.item, state=self.state, schedule_checkpoint=self._checkpoint_soon
        )
        if should_close:
            logger.info(
                "interview-worker.answer room=%s count=%d/%d — scheduling close",
                self.session_id, self.state.candidate_answer_count, MAX_CANDIDATE_ANSWERS,
            )
            # Held on the instance, not dropped on the floor: this is the NORMAL
            # close (fired after the candidate's final answer), and asyncio keeps
            # only a weak reference to it. Collected mid-flight it would cost the
            # closing line, the status write, the transcript, the scorecard AND
            # the room deletion — the candidate left connected to a dead room
            # until the reaper swept it 17 minutes later.
            self.close_task = asyncio.create_task(self._on_close(timed_out=False))

    async def _wall_clock_cap(self) -> None:
        """Fire the close path after SESSION_WALL_CLOCK_CAP_SECONDS."""
        await asyncio.sleep(SESSION_WALL_CLOCK_CAP_SECONDS)
        if not self.state.close_triggered:
            logger.warning(
                "interview-worker.timeout room=%s cap=%ds answers=%d",
                self.session_id, SESSION_WALL_CLOCK_CAP_SECONDS,
                self.state.candidate_answer_count,
            )
            await self._on_close(timed_out=True)

    async def _consent_watchdog(self, user_id: str | None) -> None:
        """DPDP §11 — end the interview if recording consent is withdrawn mid-session.

        Delegates to the module-level _run_consent_watchdog so the logic is
        unit-testable without a live LiveKit session.  See that function's
        docstring for the sentinel-value contract and fail-open/fail-closed rules.
        """
        await _run_consent_watchdog(
            user_id=user_id,
            on_close=self._on_close,
            state=self.state,
            session_id=self.session_id,
        )

    def _on_session_close(self, _event: Any) -> None:
        """Handle session close: cancel background tasks; run DB+scoring if not done.

        The teardown coroutine is launched as a real asyncio.Task and held in
        self.teardown_task.  Holding the REAL task (not the asyncio.shield
        wrapper) is critical: only a real Task has a strong reference that
        prevents GC, and only the real Task can be reliably awaited by the
        shutdown hook even after the shield wrapper is cancelled by the LiveKit
        framework's drain path.

        cap_task and consent_task are read defensively: the framework can fire
        "close" during avatar startup, before either exists.
        """
        if self.cap_task is not None:
            self.cap_task.cancel()
        if self.consent_task is not None:
            self.consent_task.cancel()
        if not self.state.close_triggered:
            # Candidate disconnected abruptly — create the REAL task first and
            # keep a strong reference to it.  asyncio.shield() is applied at
            # await-time in _await_teardown_on_shutdown, not here.
            self.teardown_task = asyncio.ensure_future(self._abrupt_close())
            logger.info(
                "interview-worker.abrupt_close_scheduled room=%s", self.session_id
            )

    async def _await_teardown_on_shutdown(self) -> None:
        """Shutdown callback — make sure _abrupt_close finishes before we exit.

        Fires when the job process is shutting down (SIGTERM / drain timeout).
        We use asyncio.shield() HERE (at await-time), wrapping the real Task held
        in self.teardown_task.  This means:
          • If the framework cancels THIS hook's coroutine, the shield absorbs the
            CancelledError but the real Task continues running to completion.
          • We then fall through to await the real task directly (unshielded) so we
            can observe completion or timeout without losing the result.
        A no-op when close was already handled by _on_close
        (state.close_triggered is True) or when no abrupt close was needed.
        """
        teardown_task = self.teardown_task
        if teardown_task is not None and not teardown_task.done():
            logger.info(
                "interview-worker.shutdown_hook_awaiting_teardown room=%s",
                self.session_id,
            )
            try:
                # Shield the real task so a CancelledError from the framework
                # drain does not propagate into the task itself — the inner
                # coroutine (_abrupt_close) must always run to completion.
                await asyncio.shield(teardown_task)
            except asyncio.CancelledError:
                # The shield wrapper was cancelled (framework draining), but
                # the real task is still running.  Wait for it with a hard
                # timeout so we don't block the process shutdown indefinitely.
                logger.info(
                    "interview-worker.shutdown_hook_shield_cancelled room=%s "
                    "— awaiting real task directly with timeout",
                    self.session_id,
                )
                try:
                    await asyncio.wait_for(teardown_task, timeout=30.0)
                except (TimeoutError, asyncio.CancelledError, Exception) as exc:
                    logger.warning(
                        "interview-worker.shutdown_hook_teardown_incomplete room=%s err=%s",
                        self.session_id, type(exc).__name__,
                    )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "interview-worker.shutdown_hook_teardown_incomplete room=%s err=%s",
                    self.session_id, type(exc).__name__,
                )


async def entrypoint(ctx: JobContext) -> None:
    """LiveKit job entrypoint — one invocation per interview room.

    Kept as a module-level coroutine with this exact signature because it is
    what ``WorkerOptions(entrypoint_fnc=...)`` is bound to and what the
    deployment entrypoints (``python -m app.worker.interview_worker``) reach.
    The lifecycle itself lives in :class:`InterviewJob`; see its docstring for
    the interview contract and ``InterviewJob.run`` for the ordering contract.
    """
    await InterviewJob(ctx).run()


# ---------------------------------------------------------------------------
# Prewarm — load the Silero VAD model once per worker process.
# ---------------------------------------------------------------------------


def _prewarm(proc: JobProcess) -> None:
    """Pre-load the Silero VAD ONNX model into the worker process's userdata.

    Called by the LiveKit framework once when the worker process starts, before
    any job is dispatched.  Loading the model here (blocking, ~1-2 s) instead of
    inside the job eliminates per-interview cold-start latency.

    Consumed by InterviewJob._build_agent_session():
        vad = ctx.proc.userdata.get("vad") or silero.VAD.load()

    Also the per-job-process hook for logging setup: this is the first thing the
    framework calls in a freshly spawned job process, and a spawned child does
    not inherit the parent's structlog configuration. Interviews run HERE, so
    this is the process the PII redaction chain most needs to cover.
    """
    _configure_worker_logging()
    logger.info("interview-worker.prewarm: loading silero VAD model")
    try:
        proc.userdata["vad"] = silero.VAD.load()
        logger.info("interview-worker.prewarm: silero VAD ready")
    except Exception as exc:  # noqa: BLE001
        # Prewarm failure is non-fatal — entrypoint() falls back to loading in-place.
        logger.warning(
            "interview-worker.prewarm: silero VAD load failed err=%s — "
            "will cold-load per job",
            type(exc).__name__,
        )


# ---------------------------------------------------------------------------
# Worker liveness heartbeat — refreshed only while the worker's own loop answers.
# ---------------------------------------------------------------------------

# livekit-agents serves a small HTTP server for health (GET /) and operational
# telemetry (GET /worker: worker_load, active_jobs, agent_name — unauthenticated).
# Its defaults are all interfaces on :8081, which contradicts the Dockerfile's
# "no port" posture and would expose that telemetry to anything sharing the
# network. Loopback is also all the heartbeat probe below needs.
_WORKER_HTTP_HOST = "127.0.0.1"
_WORKER_HTTP_PORT = 8081
_WORKER_HEALTH_URL = f"http://{_WORKER_HTTP_HOST}:{_WORKER_HTTP_PORT}/"
# Must stay well under worker_heartbeat_interval_seconds (>= 5) so a stalled
# probe cannot delay the next heartbeat cycle past the healthcheck's window.
_WORKER_HEALTH_TIMEOUT_SECONDS = 3.0


async def _worker_loop_responsive() -> bool:
    """Return True when the worker's own event loop answers its health endpoint.

    livekit-agents serves this endpoint from the same loop that runs the
    interview jobs, and answers 503 when the LiveKit connection or the inference
    process is gone.  So a timeout or a non-200 here is precisely the "worker is
    up but cannot conduct interviews" state the heartbeat must not paper over.
    """
    try:
        async with httpx.AsyncClient(timeout=_WORKER_HEALTH_TIMEOUT_SECONDS) as client:
            resp = await client.get(_WORKER_HEALTH_URL)
    except Exception as exc:  # noqa: BLE001 — any failure means "not responsive"
        logger.warning(
            "interview-worker.heartbeat: health probe failed url=%s err=%s",
            _WORKER_HEALTH_URL, type(exc).__name__,
        )
        return False
    if resp.status_code != 200:
        logger.warning(
            "interview-worker.heartbeat: health probe unhealthy status=%d",
            resp.status_code,
        )
        return False
    return True


async def _run_heartbeat() -> None:
    """Refresh the heartbeat file every N seconds, while the worker loop answers.

    The deploy cluster (docker-compose healthcheck) reads this file's mtime to
    decide if the worker has stalled:

        healthcheck:
          test: ["CMD", "python", "-c", "... age of the heartbeat file < 60 ..."]

    This coroutine runs on a dedicated thread with its own event loop, because
    ``cli.run_app()`` owns the main thread and its own loop.  That means its
    liveness says nothing about the loop that actually conducts interviews — a
    blocking call wedging that loop left this coroutine happily refreshing the
    file, so the healthcheck reported healthy while no candidate could get an
    interviewer.  Writing only after a successful probe of the worker's health
    endpoint (served ON that loop) is what makes the file mean what the
    healthcheck assumes it means.
    """
    path = settings.worker_heartbeat_path
    interval = settings.worker_heartbeat_interval_seconds
    logger.info(
        "interview-worker.heartbeat: starting path=%s interval=%ds probe=%s",
        path, interval, _WORKER_HEALTH_URL,
    )
    while True:
        try:
            if await _worker_loop_responsive():
                ts = datetime.now(tz=UTC).isoformat()
                # Use asyncio.to_thread so the write never blocks the event loop.
                await asyncio.to_thread(_write_heartbeat, path, ts)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "interview-worker.heartbeat: write failed path=%s err=%s",
                path, type(exc).__name__,
            )
        await asyncio.sleep(interval)


def _write_heartbeat(path: str, timestamp: str) -> None:
    """Write ``timestamp`` to ``path`` atomically (best-effort). Sync helper."""
    tmp_path = path + ".tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as fh:
            fh.write(timestamp)
        os.replace(tmp_path, path)
    except OSError:
        # /tmp unavailable or permission error — silently swallow; the healthcheck
        # will catch the stale/missing file independently.
        pass


# ---------------------------------------------------------------------------
# Admission-control request_fnc — reject jobs over the concurrency ceiling.
# ---------------------------------------------------------------------------


async def _request_fnc(job_request: Any) -> None:
    """Gate incoming job requests against the max-concurrent-jobs and memory ceilings.

    Called by the LiveKit framework BEFORE dispatching the job to entrypoint().
    Rejection here is clean: the framework notifies the room with a
    'worker_unavailable' event so the token/launch endpoint can detect the
    rejection and return a clear HTTP 503 to the candidate instead of silently
    leaving them in a dead LiveKit room with no interviewer.

    Two complementary guards:
    1. Concurrency cap (worker_max_concurrent_jobs > 0): reject when the
       application-tracked active-job counter meets or exceeds the ceiling.
       This is additive to load_threshold: CPU load may look low while
       network I/O (Sarvam streams) or memory fills up, so we enforce both.
    2. Memory estimation (job_memory_limit_mb > 0 AND container_memory_limit_mb
       > 0): if accepting one more job would push the estimated RSS above the
       VM's hard cap, reject pre-emptively.  A spike OOM-kill terminates ALL
       live interviews simultaneously, which is far worse than one polite
       rejection.

    ``settings.worker_max_concurrent_jobs == 0`` disables the concurrency cap
    (not recommended for production; useful for single-job dev testing).
    """
    cap = settings.worker_max_concurrent_jobs
    reason: str | None = None

    if cap > 0 and _active_jobs >= cap:
        reason = f"concurrency ceiling reached (active={_active_jobs} cap={cap})"

    if reason is None:
        mem_per_job = settings.job_memory_limit_mb
        mem_limit = settings.container_memory_limit_mb
        if mem_per_job > 0 and mem_limit > 0:
            # Conservative estimate: jobs already running × per-job RSS.
            # The actual prewarmed VAD model RSS (≈100–200 MB) is already in
            # the worker process and shared across jobs, so we only count
            # the *incremental* cost per additional job here.
            estimated_rss_mb = (_active_jobs + 1) * mem_per_job
            if estimated_rss_mb > mem_limit:
                reason = (
                    f"estimated RSS {estimated_rss_mb} MB would exceed "
                    f"container limit {mem_limit} MB"
                )

    if reason is not None:
        logger.warning(
            "interview-worker.admission_rejected active=%d — %s",
            _active_jobs, reason,
        )
        # reject() signals 'worker_unavailable' to LiveKit.  The HTTP token
        # endpoint reads the active-job count from Redis (written by
        # _publish_capacity) BEFORE issuing the join token, and returns HTTP 503
        # with a human-readable "server busy, try again" message so the candidate
        # is never silently dropped into a dead room with no interviewer.
        await job_request.reject()
        # Publish current capacity so the HTTP layer sees the latest count.
        await _publish_capacity()
        return

    # livekit-agents 1.x: accept() takes keyword-only args; the entrypoint is
    # already bound via WorkerOptions(entrypoint_fnc=...) in run().
    await job_request.accept()


def run() -> None:
    """Start the LiveKit worker with prewarm, heartbeat, and admission control.

    NO agent_name -> AUTOMATIC dispatch: the worker joins every room created.
    Each interview is its own room (named after session_id), so this is the
    correct + proven model. (Explicit agent_name dispatch did not connect
    reliably in testing 2026-05-31.)

    drain_timeout (graceful shutdown): on SIGTERM the worker deregisters (takes
    no new jobs) and waits up to this long for active interviews to finish
    before terminating them. Keep this <= the worker's compose stop_grace_period
    so Docker doesn't SIGKILL mid-drain.

    Heartbeat: a daemon thread writes the current UTC time to the heartbeat file
    every worker_heartbeat_interval_seconds, but only while this worker's health
    endpoint answers — see _run_heartbeat for why the probe is what makes the
    Docker healthcheck meaningful.

    Crash recovery: that same thread first runs reap_stale_sessions() once, which
    finalises sessions whose previous worker was hard-killed mid-interview. It
    runs THERE rather than on the interview loop so a slow Redis/DB sweep can
    never delay the worker registering and accepting its first candidate.
    """
    import threading

    # Before anything logs: the supervisor process reaps stale sessions and
    # runs the heartbeat, both of which touch session rows.
    _configure_worker_logging()

    async def _reap_then_heartbeat() -> None:
        """Sweep crashed sessions once, then refresh the heartbeat forever."""
        await reap_stale_sessions()
        await _run_heartbeat()

    def _start_heartbeat_in_thread() -> None:
        """Run the heartbeat coroutine in a dedicated event loop on a daemon thread.

        cli.run_app() blocks the main thread and owns its own event loop, so
        we spin the heartbeat in a separate daemon thread with its own loop.
        The thread is daemon so it exits automatically when the process exits.
        """
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(_reap_then_heartbeat())
        finally:
            loop.close()

    heartbeat_thread = threading.Thread(
        target=_start_heartbeat_in_thread, daemon=True, name="worker-heartbeat"
    )
    heartbeat_thread.start()

    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            request_fnc=_request_fnc,
            prewarm_fnc=_prewarm,
            # Both are explicit on purpose: livekit-agents otherwise binds all
            # interfaces, and picks a random port under `dev` — which would
            # leave the heartbeat probe above pointed at nothing.
            host=_WORKER_HTTP_HOST,
            port=_WORKER_HTTP_PORT,
            load_threshold=settings.worker_load_threshold,
            ws_url=settings.livekit_url,
            api_key=settings.livekit_api_key,
            api_secret=settings.livekit_api_secret,
            drain_timeout=settings.worker_drain_timeout_seconds,
        )
    )


if __name__ == "__main__":
    run()
