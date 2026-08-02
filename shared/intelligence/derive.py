"""Role profile derivation — cache, LLM, fallback.

``derive_role_profile`` is the one function the rest of the platform calls. It
guarantees a usable ``RoleProfile`` for any input, with no exception path:

    cache hit ─────────────────────────────────────────────► profile
    cache miss ─► taxonomy baseline ─► LLM refine ─► validate ─► cache ─► profile
                                   └─ any failure ─────────────────────► baseline

That "no exception path" property is deliberate. This runs on the live
interview hot path — the worker derives a profile before the candidate's room
connects. A role model that raises would take the interview down with it, and a
degraded-but-correct baseline interview is infinitely better than no interview.
Every failure is logged with its reason and recorded in ``profile.source``, so
degradation is visible rather than silent.

Injection, not import
---------------------
The LLM call and the cache arrive as callables. ``shared/`` cannot depend on
httpx, redis, or any provider SDK (four service images, four different pinned
dependency sets — see ``schema.py``), and injection also makes the whole path
testable without a network or a Redis.
"""

from __future__ import annotations

import hashlib
import json
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from typing import Protocol

import structlog

from shared.intelligence.prompt import (
    DERIVATION_SYSTEM_PROMPT,
    ProfileParseError,
    build_derivation_prompt,
    parse_profile_response,
)
from shared.intelligence.schema import SCHEMA_VERSION, RoleProfile, Seniority
from shared.intelligence.taxonomy import baseline_profile

log = structlog.get_logger(__name__)

# ``(system_prompt, user_prompt) -> response_text``. Async because every caller
# is already in an event loop.
LLMCaller = Callable[[str, str], Awaitable[str]]

# How long a derived profile stays valid. Long, because the inputs are hashed
# into the key — editing a JD or the required skills produces a different
# profile_id and therefore a natural cache miss. The TTL only exists so a
# prompt-tuning change eventually reaches jobs nobody has touched.
DEFAULT_TTL_SECONDS: int = 14 * 24 * 3600


class ProfileCache(Protocol):
    """Minimal async cache interface — satisfied by a thin Redis wrapper."""

    async def get(self, key: str) -> str | None: ...

    async def set(self, key: str, value: str, ttl_seconds: int) -> None: ...


class InMemoryProfileCache:
    """Bounded in-process LRU cache.

    Not a substitute for Redis — it exists for the LiveKit worker, which runs
    up to ``worker_max_concurrent_jobs`` interviews in ONE process and would
    otherwise re-derive the same profile for every candidate interviewing for
    the same job. Bounded so a long-lived worker cannot grow unboundedly across
    thousands of distinct roles.

    TTL is ignored on purpose: entries are keyed by an input hash, so a stale
    entry can only be stale with respect to a prompt change, and the process
    restarts on every deploy.
    """

    def __init__(self, max_entries: int = 256) -> None:
        self._max_entries = max_entries
        self._store: OrderedDict[str, str] = OrderedDict()

    async def get(self, key: str) -> str | None:
        value = self._store.get(key)
        if value is not None:
            self._store.move_to_end(key)
        return value

    async def set(self, key: str, value: str, ttl_seconds: int) -> None:
        self._store[key] = value
        self._store.move_to_end(key)
        while len(self._store) > self._max_entries:
            self._store.popitem(last=False)


def _normalise_for_hash(value: str) -> str:
    """Collapse whitespace and case so cosmetic edits do not bust the cache."""
    return " ".join(value.split()).strip().lower()


def compute_profile_id(
    *,
    job_title: str,
    jd_text: str = "",
    required_skills: list[str] | None = None,
    department: str = "",
    seniority: str = "mid",
    interview_type: str = "screening",
) -> str:
    """Deterministic id for a set of derivation inputs.

    Doubles as the cache key and as the audit handle recorded on every
    scorecard: given a profile_id you can prove which rubric produced a score.
    ``SCHEMA_VERSION`` is folded in so a schema bump invalidates every cached
    profile without anyone having to flush Redis.

    Skills are sorted before hashing — the same skill set in a different order
    is the same role, and treating it otherwise would halve the hit rate.
    """
    payload = {
        "v": SCHEMA_VERSION,
        "title": _normalise_for_hash(job_title),
        "jd": _normalise_for_hash(jd_text)[:4000],
        "skills": sorted(_normalise_for_hash(s) for s in (required_skills or []) if s.strip()),
        "dept": _normalise_for_hash(department),
        "level": _normalise_for_hash(seniority),
        "type": _normalise_for_hash(interview_type),
    }
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]


def cache_key(profile_id: str) -> str:
    return f"intel:roleprofile:{SCHEMA_VERSION}:{profile_id}"


def _coerce_seniority(value: str) -> Seniority:
    """Map a free-text level onto the three tiers the rubric calibrates to."""
    level = (value or "").strip().lower()
    if level in {"entry", "fresher", "junior", "trainee", "l1"}:
        return "entry"
    if level in {"senior", "lead", "principal", "staff", "l3", "expert"}:
        return "senior"
    return "mid"


async def derive_role_profile(
    *,
    job_title: str,
    jd_text: str = "",
    required_skills: list[str] | None = None,
    department: str = "",
    company_name: str = "",
    experience_level: str = "mid",
    interview_type: str = "screening",
    llm: LLMCaller | None = None,
    cache: ProfileCache | None = None,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
) -> RoleProfile:
    """Return the role model for a job. Never raises.

    Args:
        llm: async ``(system, user) -> text``. When None, the taxonomy baseline
            is returned directly — which is the correct behaviour for a
            deployment with no LLM key configured, and for tests.
        cache: optional async cache. Read AND write are both best-effort; a
            cache outage degrades to re-derivation, never to a failed
            interview.

    Returns:
        A validated ``RoleProfile``. Check ``.source`` to see whether the LLM
        contributed (``llm`` / ``hybrid``) or it is the deterministic baseline
        (``taxonomy``).
    """
    seniority = _coerce_seniority(experience_level)
    profile_id = compute_profile_id(
        job_title=job_title,
        jd_text=jd_text,
        required_skills=required_skills,
        department=department,
        seniority=seniority,
        interview_type=interview_type,
    )

    baseline = baseline_profile(
        profile_id=profile_id,
        job_title=job_title,
        jd_text=jd_text,
        required_skills=required_skills,
        department=department,
        seniority=seniority,
    )

    # ---- 1. Cache ---------------------------------------------------------
    key = cache_key(profile_id)
    if cache is not None:
        try:
            cached = await cache.get(key)
        except Exception as exc:  # noqa: BLE001 — cache must never break an interview
            log.warning("intelligence.cache.get_failed", error=type(exc).__name__)
            cached = None
        if cached:
            try:
                profile = RoleProfile.model_validate_json(cached)
            except ValueError as exc:
                # A cached blob from an older shape: log and re-derive rather
                # than serving something that will fail downstream.
                log.warning(
                    "intelligence.cache.stale_entry",
                    profile_id=profile_id,
                    error=str(exc)[:200],
                )
            else:
                log.info(
                    "intelligence.derive.cache_hit",
                    profile_id=profile_id,
                    domain_family=profile.domain_family,
                    source=profile.source,
                )
                return profile

    # ---- 2. No LLM configured — baseline is the answer, not a failure -----
    if llm is None:
        log.info(
            "intelligence.derive.baseline_only",
            profile_id=profile_id,
            domain_family=baseline.domain_family,
            reason="no_llm_caller",
        )
        return baseline

    # ---- 3. Refine via LLM ------------------------------------------------
    profile = baseline
    try:
        user_prompt = build_derivation_prompt(
            job_title=job_title,
            baseline=baseline,
            jd_text=jd_text,
            required_skills=required_skills,
            department=department,
            company_name=company_name,
            seniority=seniority,
            interview_type=interview_type,
        )
        raw = await llm(DERIVATION_SYSTEM_PROMPT, user_prompt)
        profile = parse_profile_response(raw, baseline=baseline, profile_id=profile_id)
        log.info(
            "intelligence.derive.llm_ok",
            profile_id=profile_id,
            domain_family=profile.domain_family,
            source=profile.source,
            competencies=len(profile.competencies),
        )
    except ProfileParseError as exc:
        log.warning(
            "intelligence.derive.parse_failed",
            profile_id=profile_id,
            error=str(exc)[:300],
            fallback="taxonomy_baseline",
        )
    except Exception as exc:  # noqa: BLE001 — transport, timeout, quota, anything
        log.warning(
            "intelligence.derive.llm_failed",
            profile_id=profile_id,
            error=type(exc).__name__,
            detail=str(exc)[:200],
            fallback="taxonomy_baseline",
        )

    # ---- 4. Cache the result ---------------------------------------------
    # The baseline is cached too. It is cheap to recompute, but caching it
    # stops a role whose LLM derivation keeps failing (bad key, exhausted
    # quota) from burning an LLM call on every single interview — which is
    # exactly the kind of cost leak the ₹12/session cap cannot absorb.
    if cache is not None:
        try:
            await cache.set(key, profile.model_dump_json(), ttl_seconds)
        except Exception as exc:  # noqa: BLE001
            log.warning("intelligence.cache.set_failed", error=type(exc).__name__)

    return profile
