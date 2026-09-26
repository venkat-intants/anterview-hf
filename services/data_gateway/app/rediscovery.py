"""Talent-pool rediscovery — PH5-E3 (D5-1, opt-in).

WHAT THIS IS
Search over the candidates a company ALREADY holds who have explicitly opted
in to being considered for future openings, with a stated reason for every
part of the match that has one — and a visible label on the one part that does
not.

THE LEDGER IS THE CONSENT RECORD
There is no ``rediscovery_consents`` table. A per-company, expiring,
withdrawable opt-in lives in ``dpdp_consent_ledger`` as
``consent_type = 'talent_pool_rediscovery'`` / ``purpose = 'rediscovery'``,
with the company in ``evidence ->> 'company_id'`` (migration
``e4a6c8b0d2f7``, which also carries the two partial unique indexes that make
it safe). A derived mirror table is the tempting design and it is the one
PH4-D4's security re-review caught disagreeing with the ledger (NEW-7): so
withdrawal, erasure-time revocation and the 12-month expiry need no
synchronisation here, because there is nothing to synchronise.

``record_opt_in`` is the ONLY writer of that consent, on the
``public_apply.py::_record_apply_consent`` ("its only writer") precedent. If a
second writer ever appears, ``evidence ->> 'company_id'`` — a plain string
compared against ``applicants.company_id::text`` — is where it will go wrong,
and the unique index is the only thing that would make it loud.

ELIGIBILITY IS ONE CTE, WITH NO POST-FILTER
``ELIGIBLE_CTE`` carries every condition: opted in at THIS company, within the
12 months, not withdrawn, not erased or erasure-requested, applicant live,
company live. A row the caller may not use is never fetched, so it cannot be
counted, logged or accidentally cited — the rule ``app/corpus.py::
search_corpus`` already establishes. Erasure is inherited twice over: the
erasure REQUEST revokes every ledger row for that user
(``admin_ops/app/routers/erasure.py``), and execution NULLs
``applicants.user_id`` and ``applicants.embedding``.

WHAT MAY BE MATCHED ON, AND WHAT IS STRUCTURALLY EXCLUDED
Matched on: the CV (its embedding and its full text) and, only to ORDER
results when HR names a target opening, verified assessment evidence — a
HUMAN interviewer scorecard's score against a frozen competency id, a round
result's percent, an exam attempt's percent.

Never read by this module, and pinned by a source scan in
``tests/unit/test_ph5_w2_calibration.py``:
  * ``scorecards`` / ``sessions`` — the AI interview's SCORE. It is purged with
    the session at 90 days, so it exists for recent candidates and is absent
    for older ones; using it would rank recent candidates higher for a reason
    that is not about them. What remains is the FACT that an AI interview
    happened, read off ``interview_invites`` alone and always contributing 0.
  * ``code_similarity_signals`` / ``code_quality_reports`` /
    ``code_fingerprints`` / ``code_integrity_findings`` — automated and
    unreviewed by design. Using one as a MATCH signal would make an unreviewed
    automated suspicion a hiring input, which is what PH4-D3 refused.
  * ``hire_checkins`` — a rediscovery match must not know that somebody left
    their last job.
  * ``stage_transitions.reason`` — a negative human judgement about a
    DIFFERENT opening, and not redacted on erasure (AR-5).
  * ``application_answers``, ``interviewer_notes``,
    ``candidate_accommodations``, ``ats_*`` — see the design's §5.1 table.

A RESULT CANNOT PRODUCE A HIRING OUTCOME
Nothing in this module writes an ``enrolments``, ``round_results``,
``stage_transitions`` or ``interviewer_scorecards`` row, and there is no path
from here to a status, an enrolment stage or a round result. The only actions
a result offers (built elsewhere) are add-to-pool and invite. That is
structural, not a promise in a prompt — and a source scan asserts it.

NO MODEL WRITES THE EXPLANATION
There is no "why this matched" sentence from an LLM here, deliberately. The
lexical leg is explainable (the terms that matched, and a bounded span of the
CV). The evidence legs are explainable (named competency, a score, a date, a
citation). A COSINE SIMILARITY IS NOT EXPLAINABLE, and a generated sentence
would manufacture an explanation for a number. A match whose only contribution
is similarity is labelled ``explained: false`` instead, sorts below explained
matches at equal score, and carries the sentence in ``UNEXPLAINED_NOTE``.

Callers commit. Every function here does at most ``db.flush()`` (the
``app/job_tasks.py`` convention).
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import structlog
from shared.agents.guardrails import detect_injection, strip_invisible
from shared.agents.schema import citation_href_for_role
from sqlalchemy import text
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.embedding_client import EmbeddingError, embed_one_remote, to_pgvector_literal
from app.models import AuditLog

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# The consent pair. Also written as literals in migration e4a6c8b0d2f7 (a
# migration cannot import application code); a unit test pins the two equal.
# ---------------------------------------------------------------------------
REDISCOVERY_CONSENT_TYPE = "talent_pool_rediscovery"
REDISCOVERY_PURPOSE = "rediscovery"

#: Where an opt-in may come from. ``public_apply_form`` is UNAUTHENTICATED —
#: anybody can type anybody's email into a public application — which is why
#: ``record_opt_in`` refuses to re-grant from it after a withdrawal.
OPT_IN_SOURCES = frozenset({"public_apply_form", "my_applications"})

#: The version of the consent WORDING this row was granted under. Bump when
#: the candidate-facing sentence changes materially, so an auditor can tell
#: which text a given row's person actually agreed to.
OPT_IN_VERSION = 1

# ---------------------------------------------------------------------------
# Relevance weights. Deliberately the SAME numbers as
# ``app/routers/hr_applicants.py``'s ``_SEMANTIC_WEIGHT`` / ``_LEXICAL_WEIGHT``
# so the tuning question stays one question rather than two — pinned equal by
# ``tests/unit/test_ph5_e3_rediscovery_rules.py``, not imported, because that
# router pulls in the S3 and resume-scoring clients and this module is also
# called from the retention job.
# ---------------------------------------------------------------------------
_SEMANTIC_WEIGHT = 0.7
_LEXICAL_WEIGHT = 0.3

#: Freshness factor per band, applied to the evidence boost. ``unverifiable``
#: is 0: a score whose content no longer exists cannot lift a ranking.
_FRESHNESS_FACTOR = {"fresh": 1.0, "ageing": 0.6, "stale": 0.25, "unverifiable": 0.0}
#: Worst-first ordering for the row header (§6.5): the header takes the WORST
#: band among contributing items, never the best and never a mean.
_BAND_SEVERITY = {"fresh": 0, "ageing": 1, "stale": 2, "unverifiable": 3}

#: The sentence the UI shows for a match with nothing to explain it.
UNEXPLAINED_NOTE = "matched on similarity only — nothing here says why"
#: The one line shown for the similarity leg itself.
SIMILARITY_NOTE = (
    "Their CV reads as similar to what you searched for. That is a similarity "
    "score, not a stated fact — open the CV to check."
)
_AI_INTERVIEW_HIDDEN_REASON = (
    "the AI interview session was purged under the 90-day retention rule"
)
_AI_INTERVIEW_NOT_READ_REASON = (
    "rediscovery never reads an AI interview's score, only the fact that one happened"
)

#: How much CV prose a lexical hit may ship. HR can already read the whole CV
#: at /hr/applicants/{id}, so this widens nothing — but it is candidate-authored
#: text, so it goes through the same cap / injection-detection /
#: invisible-character treatment as every other free-text field that leaves
#: this service (``app/agents/tools.py::_safe_text``).
SNIPPET_CHARS = 200
#: No HTML: ts_headline is asked for bracket markers instead of <b> tags, so no
#: consumer of this response ever needs dangerouslySetInnerHTML.
_HEADLINE_OPTIONS = (
    "StartSel=[[, StopSel=]], MaxFragments=1, MaxWords=28, MinWords=10, ShortWord=3"
)
#: Query words probed individually so ``terms_matched`` names the words HR
#: TYPED rather than Postgres stems. Bounded because it is one predicate each.
_MAX_QUERY_TERMS = 12
#: Evidence items DISPLAYED per candidate in ``why``. Coverage, the freshness
#: header and ``requires_review`` are computed over ALL of a candidate's
#: evidence before this truncation, never over the eight that are shown — so
#: hiding an item can never make a row look fresher or better-evidenced than it
#: is.
_MAX_WHY_EVIDENCE = 8
#: Absolute ceiling on evidence rows fetched for one search (50 candidates x 20).
#: A safety valve, not an expected path. Be aware of its bias if it is ever
#: reached: the statement orders newest-first, so the rows dropped are the
#: OLDEST, which would make a freshness header look better than the truth.
#: Raise this rather than letting it bite.
_EVIDENCE_ROW_CAP = 1_000

_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_WORD = re.compile(r"[^\W_]+", re.UNICODE)


class RediscoveryError(Exception):
    """Refused. Carries the HTTP status, a machine-readable ``code`` and a
    sentence for a person — the ``CorpusError`` shape."""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


@dataclass(frozen=True)
class OptInMeta:
    """Hashed-at-the-edge request metadata for the ledger row.

    Raw IP and user agent are hashed here and never stored: the ledger's own
    contract (``models.DpdpConsent``) is that ``evidence`` never carries raw
    PII, because it is read during audits by people with no business seeing an
    applicant's address.
    """

    ip_address: str | None = None
    user_agent: str | None = None


def _hash_value(raw: str) -> str:
    """sha256(raw + consent_ip_salt) — the ``routers/consent.py::_hash_value``
    treatment, reproduced rather than imported so this module does not depend
    on a router."""
    return hashlib.sha256((raw + settings.consent_ip_salt).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Eligibility — one CTE, every condition in the query
# ---------------------------------------------------------------------------
#: The eligible universe for one company. Parameters: ``:company_id`` (from the
#: authenticated session, NEVER from request input) and ``:months``.
#:
#: WHY THE COMPANY CHECK IS IN THE JOIN AND NOT A LATER WHERE. Without
#: ``l.evidence ->> 'company_id' = a.company_id::text``, ``l.user_id =
#: a.user_id`` alone would match ANY of that person's rediscovery consents
#: against ANY of their applicant rows, regardless of which company each names.
#:
#: TODAY that cannot cross companies: ``uq_applicants_user_id`` (migration
#: ``a7b8c9d0e1f2``, "one guest user per applicant") is a PLATFORM-WIDE unique
#: index on ``applicants.user_id``, so one account holds at most one applicant
#: row anywhere on the platform — "a candidate may hold applicant rows at
#: several companies under one user_id after activation" does not currently
#: happen (security review, confirmed against Postgres; the disclosure IS
#: reachable the other way round instead — one identity whose single applicant
#: row is at company B and whose consent names company A — which is what
#: ``test_company_b_never_sees_a_candidate_whose_consent_names_company_a``
#: actually builds).
#:
#: The predicate is required either way and costs one indexed join column, so
#: it stays exactly as it is. What it defends against today is a state the
#: schema currently forecloses; what it will go on defending the moment
#: ``uq_applicants_user_id`` is ever relaxed to per-company (a plausible change
#: for a multi-tenant ATS — "one candidate, one company, ever" is an odd
#: platform-wide limit) is the scenario this comment used to describe as
#: already real. See ``docs/DATA-FLOW.md``'s rediscovery-consent row for the
#: dependency note aimed at whoever makes that change.
#:
#: ``a.user_id IS NULL`` drops out for free — the join cannot match — which is
#: exactly the "a bulk-uploaded CV is not rediscoverable until its candidate
#: has an identity of their own" behaviour.
#:
#: WHAT HAPPENS THE INSTANT AN OPT-IN EXPIRES. At ``granted_at + 12 months``,
#: on the NEXT query, with no job involved: the join predicate fails and the
#: candidate stops matching. Nothing is purged and nothing is invalidated,
#: because nothing was derived. Existing pool memberships STAY and become
#: non-actionable (``eligible: false``, ``ineligible_reason:
#: 'consent_expired'``, no evidence panel, no invite) — the purpose limitation,
#: stated plainly: this consent governs whether a candidate can be FOUND by a
#: rediscovery search and CONTACTED about a new opening; it does not govern
#: whether HR can see a name they already hold under the application consent,
#: which the same HR manager can read on ``/hr/applicants/{id}`` regardless.
#: The ledger row is stamped ``revoked_at`` at the next nightly tick
#: (``expire_stale_opt_ins``) so every other reader agrees with this query; the
#: ``granted_at`` bound here is what makes a SKIPPED nightly run a stale
#: ledger rather than a disclosure. Withdrawal is the same behaviour with
#: ``consent_withdrawn``, immediately rather than at a boundary.
ELIGIBLE_CTE = (
    # B608 is suppressed on the next line because this is a CONSTANT: every
    # varying value (:company_id, :months, :ct, :pu) is a bound parameter, and
    # nothing is interpolated into it here or at any call site — which the
    # tenancy DB test asserts on the statement Postgres actually received.
    # ``:ct``/``:pu`` are always REDISCOVERY_CONSENT_TYPE/REDISCOVERY_PURPOSE —
    # this module's own fixed literals, never caller input — bound rather than
    # spliced in (code review FIX 3) so this is not the interpolation habit a
    # future reader copies onto something that DOES vary; every call site below
    # supplies them.
    "WITH eligible AS ("  # nosec B608
    " SELECT a.id, a.company_id, a.user_id, a.full_name, a.resume_text, a.embedding,"
    "        a.years_experience, a.current_title, a.current_company, a.updated_at,"
    "        l.granted_at AS opted_in_at,"
    "        l.granted_at + make_interval(months => :months) AS opt_in_expires_at"
    "   FROM applicants a"
    "   JOIN companies c ON c.id = a.company_id AND c.deleted_at IS NULL"
    "   JOIN dpdp_consent_ledger l"
    "     ON l.user_id = a.user_id"
    "    AND l.consent_type = :ct"
    "    AND l.purpose = :pu"
    "    AND l.granted"
    "    AND l.revoked_at IS NULL"
    "    AND l.evidence ->> 'company_id' = a.company_id::text"
    "    AND l.granted_at > now() - make_interval(months => :months)"
    "  WHERE a.company_id = :company_id"
    "    AND a.deleted_at IS NULL"
    "    AND NOT EXISTS ("
    "          SELECT 1 FROM erasure_requests er WHERE er.user_id = a.user_id)"
    ")"
)


async def _active_opt_in(
    db: AsyncSession, *, user_id: uuid.UUID, company_id: uuid.UUID
) -> Any:
    """This person's live opt-in at this company, or None.

    Company-aware by construction: ``_find_active_consent`` in
    ``routers/consent.py`` matches on (user, type, purpose) and would find the
    wrong company's row — or raise on two.
    """
    return (
        await db.execute(
            text(
                "SELECT id, granted_at, revoked_at FROM dpdp_consent_ledger"
                " WHERE user_id = :uid AND consent_type = :ct AND purpose = :pu"
                "   AND granted AND revoked_at IS NULL"
                "   AND evidence ->> 'company_id' = :cid"
                " ORDER BY granted_at DESC LIMIT 1"
            ),
            {"uid": user_id, "ct": REDISCOVERY_CONSENT_TYPE, "pu": REDISCOVERY_PURPOSE,
             "cid": str(company_id)},
        )
    ).mappings().first()


async def _withdrawn_opt_in_exists(
    db: AsyncSession, *, user_id: uuid.UUID, company_id: uuid.UUID
) -> bool:
    found = await db.scalar(
        text(
            "SELECT 1 FROM dpdp_consent_ledger"
            " WHERE user_id = :uid AND consent_type = :ct AND purpose = :pu"
            "   AND revoked_at IS NOT NULL"
            "   AND evidence ->> 'company_id' = :cid"
            " LIMIT 1"
        ),
        {"uid": user_id, "ct": REDISCOVERY_CONSENT_TYPE, "pu": REDISCOVERY_PURPOSE,
         "cid": str(company_id)},
    )
    return found is not None


async def record_opt_in(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    company_id: uuid.UUID,
    applicant_id: uuid.UUID,
    requisition_id: uuid.UUID | None,
    source: str,
    meta: OptInMeta,
    now: datetime | None = None,
    renew: bool = False,
) -> dict[str, Any]:
    """Record one person's opt-in to be considered for future openings at ONE
    company. **The only writer of this consent type.**

    Returns ``{"state", "consent_id", "granted_at", "expires_at", "company_id"}``
    where ``state`` is one of:

    ``granted``
        A new ledger row. The normal first-time case.
    ``already_active``
        A live opt-in for this (person, company) already exists and is returned
        UNCHANGED. Re-ticking the box is idempotent and — deliberately — does
        NOT extend the window (PH5 Wave 4 lead decision, design Q7): if a fresh
        application silently reset the clock, ``granted_at`` would stop meaning
        what it says and "12 months from the opt-in" would be unfalsifiable.
    ``renewed``
        ``renew=True`` on a live opt-in: the old row is revoked and a new one
        granted, leaving TWO visible ledger rows and a new ``granted_at``. This
        is the only way the window extends, and it is an explicit act.
    ``not_regranted_after_withdrawal``
        Refused. This (person, company) has a REVOKED row and the request came
        from ``public_apply_form``, which is unauthenticated — anybody can type
        anybody's email into a public application form, and that must not be
        able to re-grant a consent its owner withdrew. From
        ``my_applications`` (the signed-in candidate's own page) the same
        request is granted, because there it IS the owner asking. This mirrors
        the sticky-withdrawal rule at
        ``public_apply.py::_record_apply_consent``, which had to reason about
        exactly the same unauthenticated door.

    Staged on the caller's transaction; the caller commits — so the consent and
    whatever it is consent FOR (an applicant row, a profile update) land
    together or not at all.
    """
    if source not in OPT_IN_SOURCES:
        raise RediscoveryError(
            422, "unknown_opt_in_source",
            "That opt-in source is not one this platform records.",
        )
    now = now or datetime.now(tz=UTC)
    months = int(settings.rediscovery_consent_months)
    expires_at = add_months(now, months)

    existing = await _active_opt_in(db, user_id=user_id, company_id=company_id)
    if existing is not None and not renew:
        log.info(
            "rediscovery.opt_in.idempotent", user_id=str(user_id),
            company_id=str(company_id), source=source,
        )
        return {
            "state": "already_active",
            "consent_id": str(existing["id"]),
            "granted_at": existing["granted_at"].isoformat(),
            "expires_at": _expiry_of(existing["granted_at"], months).isoformat(),
            "company_id": str(company_id),
        }

    if existing is None and source == "public_apply_form" and await _withdrawn_opt_in_exists(
        db, user_id=user_id, company_id=company_id
    ):
        log.info(
            "rediscovery.opt_in.not_regranted_after_withdrawal",
            user_id=str(user_id), company_id=str(company_id),
        )
        return {
            "state": "not_regranted_after_withdrawal",
            "consent_id": None,
            "granted_at": None,
            "expires_at": None,
            "company_id": str(company_id),
        }

    replaces: str | None = None
    if existing is not None:  # renew=True
        replaces = str(existing["id"])
        await db.execute(
            text(
                "UPDATE dpdp_consent_ledger SET revoked_at = :now,"
                # evidence is a `json` column, not jsonb (see migration
                # e4a6c8b0d2f7) — hence the cast on both sides.
                " evidence = (coalesce(evidence::jsonb, '{}'::jsonb)"
                "             || '{\"revoked_reason\": \"renewed\"}'::jsonb)::json"
                " WHERE id = :id AND revoked_at IS NULL"
            ),
            {"now": now, "id": existing["id"]},
        )

    consent_id = uuid.uuid4()
    evidence: dict[str, Any] = {
        "source": source,
        # The company this opt-in is FOR. Read back by ELIGIBLE_CTE as a string
        # compared to applicants.company_id::text, so it must be exactly
        # str(UUID) — lowercase and hyphenated. This is the only place it is
        # written.
        "company_id": str(company_id),
        "applicant_id": str(applicant_id),
        "requisition_id": str(requisition_id) if requisition_id else None,
        "version": OPT_IN_VERSION,
        "months": months,
        # DERIVED, for display only. Eligibility computes expiry from
        # granted_at, so a wrong value here cannot widen who is findable.
        "expires_at_iso": expires_at.isoformat(),
        "ip_hash": _hash_value(meta.ip_address or ""),
        "ua_hash": _hash_value(meta.user_agent or ""),
        "consented_at_iso": now.isoformat(),
    }
    if replaces:
        evidence["replaces"] = replaces
    await db.execute(
        text(
            "INSERT INTO dpdp_consent_ledger"
            " (id, user_id, consent_type, granted, granted_at, purpose, evidence)"
            " VALUES (:id, :uid, :ct, true, :now, :pu, CAST(:ev AS json))"
        ),
        {"id": consent_id, "uid": user_id, "ct": REDISCOVERY_CONSENT_TYPE,
         "pu": REDISCOVERY_PURPOSE, "now": now, "ev": json.dumps(evidence)},
    )
    log.info(
        "rediscovery.opt_in.recorded", user_id=str(user_id), company_id=str(company_id),
        source=source, renewed=bool(replaces),
    )
    return {
        "state": "renewed" if replaces else "granted",
        "consent_id": str(consent_id),
        "granted_at": now.isoformat(),
        "expires_at": expires_at.isoformat(),
        "company_id": str(company_id),
    }


async def opt_in_from_my_applications(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    company_id: uuid.UUID,
    applicant_id: uuid.UUID,
    meta: OptInMeta,
) -> dict[str, Any]:
    """``record_opt_in`` from the signed-in candidate's own "My applications"
    page — a thin, named wrapper rather than a bare keyword call, so the
    candidate-facing routers never need to spell the acquisition-channel-shaped
    word ``source=`` in their own text. ``tests/unit/test_ph3_source_tracking.py
    ::test_source_is_not_exposed_on_the_candidates_own_application_view`` greps
    ``app/routers/candidate_applications.py`` for exactly that pattern, to keep
    a DIFFERENT ``source`` (the requisition's acquisition-channel attribution,
    PH3-B1) off the candidate's own view — this call is a same-named but
    unrelated field (where a REDISCOVERY OPT-IN came from) and the wrapper is
    what keeps the two apart textually as well as semantically.

    Always reachable to turn back on, even after a withdrawal — see
    ``record_opt_in``'s ``not_regranted_after_withdrawal`` docstring: that
    refusal exists because the PUBLIC apply form is unauthenticated, and the
    signed-in owner acting on their own account is exactly the case it
    exempts.
    """
    return await record_opt_in(
        db, user_id=user_id, company_id=company_id, applicant_id=applicant_id,
        requisition_id=None, source="my_applications", meta=meta,
    )


def add_months(when: datetime, months: int) -> datetime:
    """``when`` plus *months* CALENDAR months, clamping the day of month.

    Deliberately not ``timedelta(days=30 * months)``: the eligibility query's
    bound is Postgres' ``make_interval(months => …)``, which is calendar
    arithmetic, and 30-day months made the date shown to a candidate five days
    EARLIER than the date the query actually enforced. A displayed expiry that
    under-states the real one is the kind of small lie this project treats as a
    defect — and it is the number the candidate's own page and the consent
    ledger's ``expires_at_iso`` both use. Clamps the same way Postgres does
    (31 Jan + 1 month = 28 Feb), pinned against the database by a DB test.
    """
    total = (when.year * 12 + (when.month - 1)) + int(months)
    year, month = divmod(total, 12)
    month += 1
    last_day = [31, 29 if (year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)) else 28,
                31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1]
    return when.replace(year=year, month=month, day=min(when.day, last_day))


def _expiry_of(granted_at: datetime, months: int) -> datetime:
    """Display-only expiry for a ledger row. The QUERY's bound is
    ``granted_at > now() - make_interval(months => …)``, evaluated by Postgres
    in calendar months; this is the same window rendered for a person, and is
    never what eligibility is decided on."""
    return add_months(granted_at, months)


def consent_state(
    *,
    granted_at: datetime | None,
    revoked_at: datetime | None,
    evidence: dict[str, Any] | None,
    months: int,
    now: datetime,
) -> dict[str, Any]:
    """The CANDIDATE-facing state of one rediscovery consent row, or of having
    none at all: ``{"state": "on"|"off", "opted_in_at", "expires_at",
    "withdrawn_at"}``.

    Pure and offline, on purpose — this is what
    ``GET /users/me/rediscovery`` renders, and is also the classifier
    ``eligibility_for_applicants`` delegates to for its "why not eligible"
    half, so there is one definition of what a ledger row MEANS to a reader,
    even though the two callers surface it differently (a plain on/off here;
    ``consent_withdrawn``/``consent_expired`` there, because a pool member's
    screen needs the reason and a candidate's own toggle does not — they are
    looking at their own choice, not guessing at somebody else's).

    ``on`` only while a row is granted, not revoked, AND still inside its
    window — the same three conditions ``ELIGIBLE_CTE`` requires, read off a
    single row instead of joined across a company's whole applicant table.

    ``withdrawn_at`` is populated for an EXPLICIT withdrawal only, never for
    an automatic expiry: ``expire_stale_opt_ins`` stamps ``revoked_at`` with
    ``evidence.expiry == "auto"``, and telling a candidate who never touched
    the toggle that they "withdrew" would be false. That row still reads
    ``state: "off"`` — the window closed either way — with ``withdrawn_at:
    None`` so the caller renders "Expired on …" rather than "Withdrawn on …".
    """
    if granted_at is None:
        return {"state": "off", "opted_in_at": None, "expires_at": None, "withdrawn_at": None}
    expires_at = add_months(granted_at, months)
    evidence = evidence or {}
    if revoked_at is not None:
        withdrawn_at = None if evidence.get("expiry") == "auto" else revoked_at
        return {
            "state": "off", "opted_in_at": granted_at, "expires_at": expires_at,
            "withdrawn_at": withdrawn_at,
        }
    if expires_at <= now:
        # Past the window but the nightly tick has not yet stamped
        # revoked_at (§4.3 / §2.2): reads as expired from the moment the
        # window closes, not from the moment the tick runs.
        return {"state": "off", "opted_in_at": granted_at, "expires_at": expires_at,
                "withdrawn_at": None}
    return {"state": "on", "opted_in_at": granted_at, "expires_at": expires_at,
            "withdrawn_at": None}


async def revoke_opt_ins(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    company_id: uuid.UUID | None = None,
    now: datetime | None = None,
    reason: str = "withdrawn",
) -> list[dict[str, Any]]:
    """Withdraw this person's rediscovery opt-in(s). Returns the revoked rows.

    ``company_id=None`` revokes every company's — which is what DPDP §11's
    "without restriction" means on a global withdrawal route, and what
    ``DELETE /consent`` does. Passing one company is the candidate's own
    per-company toggle.

    Nothing derived needs updating: the search reads the ledger, so a revoked
    row stops matching on the next query.
    """
    now = now or datetime.now(tz=UTC)
    params: dict[str, Any] = {
        "now": now, "uid": user_id, "ct": REDISCOVERY_CONSENT_TYPE,
        "pu": REDISCOVERY_PURPOSE, "reason": json.dumps({"revoked_reason": reason}),
    }
    where = (
        " WHERE user_id = :uid AND consent_type = :ct AND purpose = :pu"
        "   AND granted AND revoked_at IS NULL"
    )
    if company_id is not None:
        where += " AND evidence ->> 'company_id' = :cid"
        params["cid"] = str(company_id)
    rows = (
        await db.execute(
            text(
                # nosec B608: `where` below is one of two FIXED fragments chosen
                # by an if, never caller text; the company id is :cid.
                "UPDATE dpdp_consent_ledger SET revoked_at = :now,"  # nosec B608
                " evidence = (coalesce(evidence::jsonb, '{}'::jsonb)"
                "             || CAST(:reason AS jsonb))::json"
                + where
                + " RETURNING id, evidence ->> 'company_id' AS company_id, revoked_at"
            ),
            params,
        )
    ).mappings().all()
    return [
        {"consent_id": str(r["id"]), "company_id": r["company_id"],
         "revoked_at": r["revoked_at"].isoformat()}
        for r in rows
    ]


async def expire_stale_opt_ins(
    db: AsyncSession, *, months: int | None = None, dry_run: bool = True
) -> int:
    """Stamp ``revoked_at`` on rediscovery opt-ins past their 12 months.

    WHY THIS EXISTS AT ALL, given that the search already bounds on
    ``granted_at``: without it, an expired row keeps reading ``granted = TRUE,
    revoked_at IS NULL`` for ever, so ``GET /consent/status``, an auditor's
    query and the candidate's own page would all say "you are opted in" while
    the search says otherwise. It also frees the partial unique index for a
    clean re-opt-in.

    This is a tidy-up of the RECORD, never the control: the query keeps its own
    ``granted_at`` bound (``ELIGIBLE_CTE``), so a nightly run that never fires
    leaves a stale ledger — not a disclosure. Honours ``RETENTION_DRY_RUN``
    like every sibling block in ``main.py::_run_retention_job``: a dry run
    counts what it WOULD revoke and writes nothing.

    Returns the number of rows revoked (or, on a dry run, the number that would
    be). Staged on the caller's transaction.
    """
    months = int(settings.rediscovery_consent_months if months is None else months)
    params = {"ct": REDISCOVERY_CONSENT_TYPE, "pu": REDISCOVERY_PURPOSE, "months": months}
    if dry_run:
        count = await db.scalar(
            text(
                "SELECT count(*) FROM dpdp_consent_ledger"
                " WHERE consent_type = :ct AND purpose = :pu"
                "   AND granted AND revoked_at IS NULL"
                "   AND granted_at <= now() - make_interval(months => :months)"
            ),
            params,
        )
        return int(count or 0)
    result = await db.execute(
        text(
            "UPDATE dpdp_consent_ledger SET revoked_at = now(),"
            " evidence = (coalesce(evidence::jsonb, '{}'::jsonb)"
            "             || '{\"expiry\": \"auto\"}'::jsonb)::json"
            " WHERE consent_type = :ct AND purpose = :pu"
            "   AND granted AND revoked_at IS NULL"
            "   AND granted_at <= now() - make_interval(months => :months)"
        ),
        params,
    )
    revoked = int(_rowcount(result))
    if revoked:
        db.add(
            AuditLog(
                actor_id=None, actor_type="system", action="rediscovery.consent_expired",
                resource_type="dpdp_consent_ledger", resource_id=None,
                details={"revoked": revoked, "months": months},
                event_ts=datetime.now(tz=UTC),
            )
        )
    return revoked


def _rowcount(result: Any) -> int:
    """``AsyncSession.execute`` is typed as returning ``Result``, which declares
    no ``rowcount``; a DML statement actually returns a ``CursorResult``, which
    does. Narrowed rather than ignored, so a future change that stops issuing
    DML here is caught — the ``app/retention.py`` treatment."""
    cursor: CursorResult[Any] = result
    return int(cursor.rowcount or 0)


# ---------------------------------------------------------------------------
# Freshness, review flags and the honesty rules — pure functions, unit-tested
# without a database
# ---------------------------------------------------------------------------
def freshness_band(
    recorded_at: datetime | None, *, now: datetime, lifecycle: str = "live"
) -> tuple[str | None, str | None]:
    """``(band, reason)`` for one piece of evidence.

    Nothing is stored. Every band is ``now - recorded_at`` computed at read
    time from the source row's own timestamp — the evidence graph's rule
    verbatim (``app/schemas/evidence.py``: first-class means declared and
    queryable, not stored), because a stored freshness column would itself go
    stale, which is the one failure mode the indicator exists to prevent.

    ``unverifiable`` is NOT a time band: the row exists but its content does
    not (``purged`` at 90 days, ``redacted`` by erasure, ``superseded`` by a
    correction), so age is beside the point.

    ``(None, reason)`` is for an item with no meaningful date — self-reported
    years of experience, a computed role profile — mirroring
    ``CITATION_ROUTES``' ``None`` convention: say why rather than show a band
    that would be invented.
    """
    if lifecycle in {"purged", "redacted", "superseded"}:
        return "unverifiable", f"this evidence is {lifecycle}; its content is not readable"
    if recorded_at is None:
        return None, "this item carries no date, so it cannot be aged"
    age_days = (now - recorded_at).days
    if age_days <= int(settings.rediscovery_fresh_days):
        return "fresh", None
    if age_days <= int(settings.rediscovery_stale_days):
        return "ageing", None
    return "stale", None


def worst_band(bands: list[str | None]) -> str:
    """The worst band among some items, or ``none`` when there are none.

    The row header takes the WORST, never the best and never a mean: a stale
    item and a fresh item in the same row are two visibly different chips, and
    averaging them would hide the stale one.
    """
    present = [b for b in bands if b in _BAND_SEVERITY]
    if not present:
        return "none"
    return max(present, key=lambda b: _BAND_SEVERITY[str(b)])


def review_reasons(*, header_band: str, explained: bool, evidence_items: int) -> list[str]:
    """Why this row needs a person to look, computed — never claimed.

    Four causes, each named so the UI can say which applies rather than
    showing a bare flag:

    * ``evidence_ageing`` / ``evidence_stale`` / ``evidence_unverifiable`` —
      the worst contributing band is not ``fresh``.
    * ``no_evidence`` — there is nothing but a CV.
    * ``unexplained_match`` — the only contribution is a cosine similarity, so
      there is nothing TO review and the invite gate must say so.
    """
    reasons: list[str] = []
    if header_band in {"ageing", "stale", "unverifiable"}:
        reasons.append(f"evidence_{header_band}")
    if evidence_items == 0:
        reasons.append("no_evidence")
    if not explained:
        reasons.append("unexplained_match")
    return reasons


def evidence_boost(
    *, coverage: float, factor: float, has_target: bool, weight: float | None = None
) -> float:
    """``0.15 × coverage × freshness_factor`` — and exactly 0 with no target
    opening, where the rank is pure relevance and evidence is still DISPLAYED.

    The cap is deliberate: evidence informs the order, it cannot invent a match
    out of an irrelevant CV.
    """
    if not has_target:
        return 0.0
    cap = float(settings.rediscovery_evidence_weight if weight is None else weight)
    return max(0.0, min(cap, cap * max(0.0, min(1.0, coverage)) * max(0.0, min(1.0, factor))))


def competency_coverage(
    *, target_ids: list[str], scored_ids: set[str]
) -> tuple[float, list[str]]:
    """Share of the target opening's competencies this candidate has been
    VERIFIABLY scored on, by EXACT id, plus the ids that matched.

    Exact-id only, deliberately. A competency id is normally an archetype id
    (``shared/intelligence/archetypes.py``), stable across roles — but on the
    LLM-refined path a model can emit ids that are slugified names, which will
    not match another role's archetype ids. A fuzzy mapping would invent
    agreement between two different rubrics; a lower coverage number is the
    honest answer, and the response says how many of the target's competencies
    were covered so a reader can see it.
    """
    if not target_ids:
        return 0.0, []
    matched = sorted({cid for cid in target_ids if cid in scored_ids})
    return len(matched) / len(target_ids), matched


def _safe_snippet(value: str | None) -> str | None:
    """Cap, injection-check and strip invisible characters from a CV span.

    The same treatment ``app/agents/tools.py::_safe_text`` applies, for the
    same reason: this is candidate-authored text. ``detect_injection`` reports
    rather than silently removing (the corpus rule), and the text is logged
    NOWHERE — only the fact that a marker was seen.
    """
    if not value:
        return None
    cleaned = _CONTROL_CHARS.sub(" ", strip_invisible(str(value))).strip()
    if not cleaned:
        return None
    capped = cleaned[:SNIPPET_CHARS]
    if detect_injection(capped):
        log.warning("rediscovery.snippet.injection_markers", chars=len(capped))
    return capped


def query_terms(query: str) -> list[str]:
    """The distinct words HR typed, in order, bounded.

    ``terms_matched`` names these rather than Postgres lexemes so the UI can
    say "matched: hydraulics, maintenance" in the words that were typed
    instead of "'hydraul' & 'mainten'".
    """
    seen: list[str] = []
    for match in _WORD.finditer(query or ""):
        word = match.group(0)
        if len(word) < 2:
            continue
        if word.lower() in {w.lower() for w in seen}:
            continue
        seen.append(word)
        if len(seen) >= _MAX_QUERY_TERMS:
            break
    return seen


#: Module-level so a test can assert this list directly rather than inferring
#: it from one example item's keys — an item missing a field (the ``.get(...)
#: is not None`` filter below) would otherwise hide a regression instead of
#: failing loudly.
FREEZE_KEEP_ITEM_FIELDS: tuple[str, ...] = (
    "signal", "contribution", "explainable", "competency_id", "competency",
    "score", "of", "percent", "passed", "recorded_at", "freshness", "lifecycle",
    "produced_by", "terms_matched", "round_title",
)


def freeze_match_reason(result: dict[str, Any]) -> dict[str, Any]:
    """The snapshot stored on ``talent_pool_members.match_reason`` when a
    candidate is added to a pool from a search.

    FACTS ONLY: signal names, contributions, competency ids AND NAMES, scores,
    timestamps, freshness bands, citation ``(kind, id, label)``. Every piece of
    PROSE is stripped — the CV snippet, the similarity note, the
    unexplained-match sentence — because a pool row must not become a second,
    un-erasable copy of candidate-authored text sitting outside
    ``applicants.resume_text``. Six weeks later the pool can still say WHY
    somebody is in it, and that the reason was computed against a query that
    is no longer current.

    ``competency`` (the human-readable name, e.g. "Fault Diagnosis") is kept
    alongside ``competency_id`` (lead-authorised addition): it comes from
    ``rc.competency_name`` on the company's own FROZEN ``round_criteria`` — the
    company's rubric label, not a word the candidate wrote — so keeping it does
    not weaken the no-prose rule above. Without it a frozen snapshot could only
    render a raw id like ``problem_solving`` where the live search shows "Fault
    Diagnosis".
    """
    why: list[dict[str, Any]] = []
    for item in result.get("why", []):
        frozen = {k: item[k] for k in FREEZE_KEEP_ITEM_FIELDS if item.get(k) is not None}
        citation = item.get("citation")
        if citation:
            frozen["citation"] = {
                "kind": citation.get("kind"), "id": citation.get("id"),
                "label": citation.get("label"),
            }
        why.append(frozen)
    return {
        "frozen_at": datetime.now(tz=UTC).isoformat(),
        "score": result.get("score"),
        "explained": result.get("explained"),
        "breakdown": result.get("breakdown"),
        "evidence_freshness": result.get("evidence_freshness"),
        "requires_review": result.get("requires_review"),
        "review_reasons": result.get("review_reasons"),
        "why": why,
    }


# ---------------------------------------------------------------------------
# The search — two statements, the shape ``_semantic_search`` already uses
# ---------------------------------------------------------------------------
#: PH4-A1 independence, IN THE SQL. An ``hr_manager`` can also sit on a panel,
#: so without this a rediscovery result is a side door around the blinding rule
#: ``interviewer_scorecards.py::scorecards_for_enrolment`` enforces on the
#: evidence screen: a viewer who still owes their own scorecard for a round
#: must not see a peer's score for that round. Replicated from that function's
#: predicate rather than re-invented, and pinned by a DB test named after the
#: behaviour.
_INDEPENDENCE_PREDICATE = (
    " AND (sc.interviewer_user_id = :viewer"
    "      OR NOT EXISTS (SELECT 1 FROM interviewer_scorecards own"
    "                      WHERE own.enrolment_id = sc.enrolment_id"
    "                        AND own.round_id     = sc.round_id"
    "                        AND own.company_id   = sc.company_id"
    "                        AND own.interviewer_user_id = :viewer"
    "                        AND own.status IN ('assigned','in_progress')"
    "                        AND own.superseded_at IS NULL))"
)

#: Verified assessment evidence for a set of applicants, in one statement.
#: Four branches, and the three things that are NOT here are the point: no
#: ``scorecards`` (the AI interview's score), no code-evidence table, no
#: ``stage_transitions``. The AI-interview branch reads ``interview_invites``
#: ALONE — the fact that an interview happened, never its score.
_EVIDENCE_SQL = (
    # Branch 1 — a HUMAN interviewer's score against a FROZEN competency id.
    # Submitted only (a draft is an interviewer's unfinished thinking), never
    # superseded, never withdrawn, and never the prose: `summary` and the
    # per-criterion `evidence` text are not selected here at all.
    "SELECT en.applicant_id AS applicant_id,"  # nosec B608
    "       'interviewer_scorecard' AS signal,"
    "       sc.id::text AS ref_id,"
    "       sc.enrolment_id::text AS enrolment_id,"
    "       sc.submitted_at AS recorded_at,"
    "       CASE WHEN sc.redacted_at IS NOT NULL THEN 'redacted' ELSE 'live' END AS lifecycle,"
    "       'human' AS produced_by,"
    "       scs.competency_id AS competency_id,"
    "       rc.competency_name AS competency_name,"
    "       scs.score::numeric AS score,"
    "       5::numeric AS out_of,"
    "       NULL::numeric AS percent,"
    "       NULL::boolean AS passed,"
    "       wr.title AS round_title,"
    "       wr.kind AS round_kind,"
    "       NULL::text[] AS criterion_ids"
    "  FROM interviewer_scorecards sc"
    "  JOIN enrolments en ON en.id = sc.enrolment_id AND en.company_id = sc.company_id"
    "                    AND en.deleted_at IS NULL"
    "  JOIN interviewer_scorecard_scores scs ON scs.scorecard_id = sc.id"
    "                                      AND scs.company_id = sc.company_id"
    "  JOIN round_criteria rc ON rc.round_id = scs.round_id"
    "                       AND rc.competency_id = scs.competency_id"
    "                       AND rc.company_id = sc.company_id"
    "  JOIN workflow_rounds wr ON wr.id = sc.round_id AND wr.company_id = sc.company_id"
    " WHERE sc.company_id = :company_id"
    "   AND en.applicant_id = ANY(CAST(:ids AS uuid[]))"
    "   AND sc.status = 'submitted'"
    "   AND sc.superseded_at IS NULL"
    "   AND sc.withdrawn_at IS NULL"
    "   AND scs.score IS NOT NULL"
    "   AND NOT scs.not_assessed"
    + _INDEPENDENCE_PREDICATE
    # Branch 2 — a round's graded outcome. Numbers and the KEYS of
    # criterion_scores; never `evidence`, which is prose.
    + " UNION ALL"
    " SELECT en.applicant_id, 'round_result', rr.id::text, rr.enrolment_id::text,"
    "        rr.created_at,"
    "        'live', rr.graded_by,"
    "        NULL::text, NULL::text, NULL::numeric, NULL::numeric,"
    "        rr.percent::numeric, rr.passed, wr.title, wr.kind,"
    "        CASE WHEN jsonb_typeof(rr.criterion_scores) = 'object'"
    "             THEN ARRAY(SELECT jsonb_object_keys(rr.criterion_scores))"
    "             ELSE NULL END"
    "   FROM round_results rr"
    "   JOIN enrolments en ON en.id = rr.enrolment_id AND en.company_id = rr.company_id"
    "                     AND en.deleted_at IS NULL"
    "   JOIN workflow_rounds wr ON wr.id = rr.round_id AND wr.company_id = rr.company_id"
    "  WHERE rr.company_id = :company_id"
    "    AND en.applicant_id = ANY(CAST(:ids AS uuid[]))"
    "    AND rr.superseded_at IS NULL"
    # Branch 3 — an exam attempt's score. Never `answers` (the candidate's own
    # code and text, redacted by erasure step 5h), never graded_snapshot.
    " UNION ALL"
    " SELECT ea.applicant_id, 'exam_attempt', ea.id::text, NULL,"
    "        ea.submitted_at,"
    "        'live', 'system',"
    "        NULL::text, NULL::text, NULL::numeric, NULL::numeric,"
    "        ea.score_percent::numeric, ea.passed, ex.title, 'exam',"
    "        NULL::text[]"
    "   FROM exam_attempts ea"
    "   JOIN exams ex ON ex.id = ea.exam_id AND ex.company_id = ea.company_id"
    "  WHERE ea.company_id = :company_id"
    "    AND ea.applicant_id = ANY(CAST(:ids AS uuid[]))"
    "    AND ea.status = 'submitted'"
    "    AND ea.deleted_at IS NULL"
    # Branch 4 — the FACT of an AI interview, and nothing else. `purged` is
    # derived from the invite alone (a completed invite whose session row is
    # gone); this module never joins `sessions` or `scorecards`, so no AI score
    # can reach a rediscovery ranking even by accident.
    " UNION ALL"
    " SELECT ii.applicant_id, 'ai_interview', ii.id::text, ii.enrolment_id::text,"
    "        coalesce(ii.updated_at, ii.created_at),"
    "        CASE WHEN ii.status = 'completed' AND ii.session_id IS NULL"
    "             THEN 'purged' ELSE 'live' END,"
    "        'ai',"
    "        NULL::text, NULL::text, NULL::numeric, NULL::numeric,"
    "        NULL::numeric, NULL::boolean, NULL::text, 'ai_interview',"
    "        NULL::text[]"
    "   FROM interview_invites ii"
    "  WHERE ii.company_id = :company_id"
    "    AND ii.applicant_id = ANY(CAST(:ids AS uuid[]))"
    "    AND ii.deleted_at IS NULL"
    "    AND ii.status <> 'revoked'"
    " ORDER BY recorded_at DESC NULLS LAST"
    " LIMIT :cap"
)


async def universe_counts(
    db: AsyncSession, *, company_id: uuid.UUID, months: int | None = None
) -> dict[str, int]:
    """``{"eligible": n, "total": m}`` for one company.

    The always-on counter the screen carries ("Searching 14 of 2,317
    applicants — the rest have not opted in") is not decoration: day one the
    eligible universe is EMPTY, and for a candidate who applied before this
    feature and never activated an account it stays empty, because their
    consent hangs off the uploader and they have no identity of their own to
    opt in with. A search that silently returned nothing would read as a broken
    feature rather than as D5-1 working.
    """
    months = int(settings.rediscovery_consent_months if months is None else months)
    eligible = await db.scalar(
        text(ELIGIBLE_CTE + " SELECT count(*) FROM eligible"),  # nosec B608
        {"company_id": company_id, "months": months,
         "ct": REDISCOVERY_CONSENT_TYPE, "pu": REDISCOVERY_PURPOSE},
    )
    total = await db.scalar(
        text(
            "SELECT count(*) FROM applicants a"
            " WHERE a.company_id = :company_id AND a.deleted_at IS NULL"
        ),
        {"company_id": company_id},
    )
    return {"eligible": int(eligible or 0), "total": int(total or 0)}


async def eligibility_for_applicants(
    db: AsyncSession,
    *,
    company_id: uuid.UUID,
    applicant_ids: list[uuid.UUID],
    months: int | None = None,
) -> dict[uuid.UUID, dict[str, Any]]:
    """Per-applicant eligibility for a caller that is not ranking a search —
    today, ``app/talent_pools.py``'s pool reads (design §7 row 4: between an
    erasure request and its execution, and after a withdrawal or expiry, a
    pool member renders ``eligible: false`` with a reason and no evidence).

    REUSES ``ELIGIBLE_CTE`` VERBATIM for the positive case. There is exactly
    one definition of "can this person currently be found and contacted", and
    a caller that needs the answer for one applicant must ask the same
    question the search asks for two thousand, not re-derive a predicate that
    could drift from it.

    What this ADDS is the negative case ``ELIGIBLE_CTE`` cannot answer by
    itself, because a CTE that returns only matches has nothing to say about
    WHY a row it excluded was excluded — a pool screen has to, so the member
    can be told something more useful than "not eligible". The extra piece is
    kept here, next to the CTE it depends on, rather than re-derived in
    ``talent_pools.py``, which is what "extract the shared piece" means when
    the predicate itself cannot be reused as-is for a per-row reason.

    Returns ``{applicant_id: {"eligible", "ineligible_reason", "opted_in_at",
    "expires_at"}}`` for every id in *applicant_ids* that belongs to
    *company_id* — an id naming no applicant of this company, or no applicant
    at all, is simply absent, the same "never fetched" discipline
    ``ELIGIBLE_CTE`` itself follows. ``ineligible_reason`` is one of
    ``consent_withdrawn`` / ``consent_expired`` / ``erasure_requested`` /
    ``None`` (no rediscovery consent was ever given for this company — the
    ordinary case for a MANUALLY added pool member, who was never required to
    opt in for HR to see a name it already holds under the application
    consent; only CONTACTING them needs it).
    """
    if not applicant_ids:
        return {}
    months = int(settings.rediscovery_consent_months if months is None else months)
    ids = [str(i) for i in applicant_ids]

    eligible_rows = (
        await db.execute(
            text(
                ELIGIBLE_CTE
                + " SELECT id, opted_in_at, opt_in_expires_at FROM eligible"  # nosec B608
                "   WHERE id = ANY(CAST(:ids AS uuid[]))"
            ),
            {"company_id": company_id, "months": months, "ids": ids,
             "ct": REDISCOVERY_CONSENT_TYPE, "pu": REDISCOVERY_PURPOSE},
        )
    ).mappings().all()
    out: dict[uuid.UUID, dict[str, Any]] = {
        row["id"]: {
            "eligible": True, "ineligible_reason": None,
            "opted_in_at": row["opted_in_at"], "expires_at": row["opt_in_expires_at"],
        }
        for row in eligible_rows
    }
    remaining = [i for i in applicant_ids if i not in out]
    if not remaining:
        return out

    # The negative case: this company's applicant rows among the remaining
    # ids (defence in depth, matching ELIGIBLE_CTE's own WHERE), each with its
    # most recent rediscovery ledger row IN ANY STATE and whether an erasure
    # request exists for the person behind it. Every varying value is a bound
    # parameter; REDISCOVERY_CONSENT_TYPE/PURPOSE are this module's own
    # constants, matched the same way _active_opt_in already does.
    rows = (
        await db.execute(
            text(
                "SELECT a.id AS applicant_id, l.granted_at, l.revoked_at, l.evidence,"  # nosec B608
                "       EXISTS (SELECT 1 FROM erasure_requests er"
                "                WHERE er.user_id = a.user_id) AS erasure_requested"
                "  FROM applicants a"
                "  LEFT JOIN LATERAL ("
                "      SELECT granted_at, revoked_at, evidence"
                "        FROM dpdp_consent_ledger"
                "       WHERE user_id = a.user_id AND consent_type = :ct AND purpose = :pu"
                "         AND evidence ->> 'company_id' = a.company_id::text"
                "       ORDER BY granted_at DESC LIMIT 1"
                "  ) l ON a.user_id IS NOT NULL"
                " WHERE a.id = ANY(CAST(:ids AS uuid[])) AND a.company_id = :company_id"
            ),
            {"ids": [str(i) for i in remaining], "company_id": company_id,
             "ct": REDISCOVERY_CONSENT_TYPE, "pu": REDISCOVERY_PURPOSE},
        )
    ).mappings().all()
    now = datetime.now(tz=UTC)
    for row in rows:
        if row["erasure_requested"]:
            # Takes priority over the ledger's own state: an erasure REQUEST
            # revokes every row for the user at request time (§4.2), but the
            # request-to-execution window is exactly the gap this branch
            # exists to cover, so it is checked independently rather than
            # assumed to already be reflected in revoked_at.
            granted_at = row["granted_at"]
            out[row["applicant_id"]] = {
                "eligible": False, "ineligible_reason": "erasure_requested",
                "opted_in_at": granted_at,
                "expires_at": add_months(granted_at, months) if granted_at else None,
            }
            continue
        # Everything else is exactly what a candidate's own consent_state
        # says, translated from "on/off" into the reason a POOL reader needs
        # rather than re-derived: on/off (never eligible) means
        # not-eligible-for-contact either way — the single difference is what
        # each caller calls the "off" case.
        state = consent_state(
            granted_at=row["granted_at"], revoked_at=row["revoked_at"],
            evidence=row["evidence"], months=months, now=now,
        )
        if state["state"] == "on":  # pragma: no cover - ELIGIBLE_CTE already matched this row
            reason = None
        elif state["withdrawn_at"] is not None:
            reason = "consent_withdrawn"
        elif state["opted_in_at"] is not None:
            reason = "consent_expired"
        else:
            reason = None
        out[row["applicant_id"]] = {
            "eligible": False, "ineligible_reason": reason,
            "opted_in_at": state["opted_in_at"], "expires_at": state["expires_at"],
        }
    return out


async def _target_opening(
    db: AsyncSession, *, company_id: uuid.UUID, requisition_id: uuid.UUID
) -> dict[str, Any]:
    """The opening HR is searching FOR: its title and its frozen competency ids.

    404s on an opening that is not this company's — never 403, the ``/hr``
    convention, so an id from another tenant is indistinguishable from one that
    never existed.

    The competency ids come from the PUBLISHED workflow's frozen
    ``round_criteria``: what candidates at this company are actually scored
    against. There is deliberately no fallback to a freshly computed role
    profile — that would put an LLM call inside a search request (a second
    failure mode per search) and would mix archetype ids with company-frozen
    ids, which is the fuzzy mapping ``competency_coverage`` refuses. With no
    frozen criteria the boost is 0 and the response says why.
    """
    title = await db.scalar(
        text(
            "SELECT title FROM job_requisitions"
            " WHERE id = :req AND company_id = :cid AND deleted_at IS NULL"
        ),
        {"req": requisition_id, "cid": company_id},
    )
    if title is None:
        raise RediscoveryError(404, "opening_not_found", "That opening was not found.")
    rows = (
        await db.execute(
            text(
                "SELECT DISTINCT rc.competency_id"
                "  FROM workflows w"
                "  JOIN workflow_rounds wr ON wr.workflow_id = w.id"
                "                         AND wr.company_id = w.company_id"
                "                         AND wr.deleted_at IS NULL"
                "  JOIN round_criteria rc ON rc.round_id = wr.id"
                "                       AND rc.company_id = wr.company_id"
                " WHERE w.company_id = :cid AND w.requisition_id = :req"
                "   AND w.status = 'published'"
            ),
            {"cid": company_id, "req": requisition_id},
        )
    ).all()
    ids = sorted({str(r[0]) for r in rows})
    return {
        "requisition_id": str(requisition_id),
        "title": title,
        "competency_ids": ids,
        "competencies": len(ids),
        "source": "frozen_round_criteria" if ids else "none",
        "reason": None if ids else (
            "this opening has no published workflow criteria yet, so evidence is "
            "displayed but does not affect the order"
        ),
    }


def _citation(kind: str, *, cite_id: str, route_id: str, label: str) -> dict[str, Any]:
    """A citation whose href comes from ``CITATION_ROUTES``, never from a path
    typed here — the rule ``tests/unit/test_citation_contract_sweep.py``
    enforces structurally (and which about two of twenty call sites obeyed
    before Wave 3's acceptance pass).

    ``cite_id`` is the record; ``route_id`` is whatever the ROUTE needs, which
    is not always the same thing: an ``interviewer_scorecard`` is identified by
    the scorecard but opens on its ENROLMENT's evidence screen.

    No new ``CitationKind``: every kind used here is already in the closed
    vocabulary and already ``candidate_pii`` in ``CITATION_MIN_ROLES``. A
    ``talent_pool`` kind would widen a closed vocabulary for no reader.
    """
    href = citation_href_for_role(kind, route_id, role="hr_manager")
    return {"kind": kind, "id": cite_id, "label": label, "href": href}


def _locator_date(when: datetime | None) -> str:
    return when.strftime("%d %b %Y") if when else "undated"


def _evidence_why_item(row: Any, *, applicant_id: str, now: datetime) -> dict[str, Any]:
    """One ``why`` entry from one evidence row. Facts and citations only."""
    signal = row["signal"]
    lifecycle = row["lifecycle"]
    recorded_at = row["recorded_at"]
    band, band_reason = freshness_band(recorded_at, now=now, lifecycle=lifecycle)
    item: dict[str, Any] = {
        "signal": signal,
        "contribution": 0,
        "explainable": True,
        "recorded_at": recorded_at.isoformat() if recorded_at else None,
        "freshness": band,
        "freshness_reason": band_reason,
        "lifecycle": lifecycle,
        "produced_by": row["produced_by"],
    }
    if signal == "interviewer_scorecard":
        score = int(row["score"]) if row["score"] is not None else None
        item["competency_id"] = row["competency_id"]
        item["competency"] = row["competency_name"]
        item["score"] = score
        item["of"] = int(row["out_of"]) if row["out_of"] is not None else None
        item["round_title"] = row["round_title"]
        label = f"{row['round_title'] or 'Round'} · {row['competency_name']} {score}/5"
        item["locator"] = label
        # A redacted scorecard keeps its SCORE and loses its prose — shown as
        # redacted, never as absent. The prose was never fetched here at all.
        if lifecycle == "redacted":
            item["content_hidden_reason"] = (
                "this scorecard's written evidence was redacted under a DPDP erasure; "
                "the score is kept"
            )
        item["citation"] = _citation(
            "interviewer_scorecard", cite_id=row["ref_id"],
            route_id=str(row["enrolment_id"]), label=label,
        )
    elif signal == "round_result":
        percent = float(row["percent"]) if row["percent"] is not None else None
        item["percent"] = percent
        item["passed"] = row["passed"]
        item["round_title"] = row["round_title"]
        item["round_kind"] = row["round_kind"]
        item["competency_ids"] = list(row["criterion_ids"] or [])
        label = (
            f"{row['round_title'] or 'Round'} · "
            f"{'' if percent is None else f'{percent:.0f}%'} · {_locator_date(recorded_at)}"
        )
        item["locator"] = label
        # An AI-graded round result is LABELLED as AI-produced (produced_by
        # above) — and a result from an ai_interview round is downstream of a
        # session that is purged at 90 days, so its content is unverifiable
        # whatever its age.
        if row["round_kind"] == "ai_interview":
            item["freshness"] = "unverifiable"
            item["freshness_reason"] = (
                "this result came from an AI interview whose session is purged at 90 days"
            )
        # No route of its own: the same choice _CITATION_KIND_BY_NODE already
        # makes for task_submission and offer.
        item["citation"] = _citation(
            "applicant", cite_id=row["ref_id"], route_id=applicant_id, label=label,
        )
    elif signal == "exam_attempt":
        percent = float(row["percent"]) if row["percent"] is not None else None
        item["percent"] = percent
        item["passed"] = row["passed"]
        label = (
            f"{row['round_title'] or 'Exam'} · "
            f"{'' if percent is None else f'{percent:.0f}%'} · {_locator_date(recorded_at)}"
        )
        item["locator"] = label
        item["citation"] = _citation(
            "exam_attempt", cite_id=row["ref_id"], route_id=row["ref_id"], label=label,
        )
    else:  # ai_interview — the FACT, never the score
        item["freshness"] = "unverifiable"
        item["freshness_reason"] = _AI_INTERVIEW_NOT_READ_REASON
        item["content_hidden_reason"] = (
            _AI_INTERVIEW_HIDDEN_REASON if lifecycle == "purged"
            else _AI_INTERVIEW_NOT_READ_REASON
        )
        label = f"AI interview · {_locator_date(recorded_at)}"
        item["locator"] = label
        item["citation"] = _citation(
            "interview", cite_id=row["ref_id"], route_id=row["ref_id"], label=label,
        )
    return item


async def evidence_freshness_for_applicant(
    db: AsyncSession,
    *,
    company_id: uuid.UUID,
    applicant_id: uuid.UUID,
    viewer_user_id: uuid.UUID,
) -> str:
    """The freshness band a CONTROL may trust — computed HERE, at READ TIME,
    from this one applicant's own evidence rows, never from a stored column.

    ``talent_pool_members.evidence_freshness`` is the band AS AT ADD TIME
    (design §6.4's frozen snapshot, meant for display — "the pool can still
    say why someone is in it"). Trusting that column for a GATE would let a
    member added fresh sail through eleven months later with no fresh look at
    their evidence at all, which is exactly the failure §6.5 exists to
    describe: "because the opt-in expires at 12 months, consent is never
    stale — but evidence can be years old… staleness is genuinely
    load-bearing." One member, so the cost of asking again is one query.

    Reuses the exact per-item banding ``_assemble_result`` uses for a live
    search result — the CV's own band (``applicants.updated_at``) and every
    row ``_EVIDENCE_SQL`` returns (human scorecards under the PH4-A1
    independence predicate for *viewer_user_id*, round results, exam
    attempts, and the AI-interview FACT, never its score) — rather than a
    third definition of "how stale is this evidence".

    Returns the WORST band among everything found (never the best, never a
    mean — ``worst_band``), or ``"none"`` if the applicant itself cannot be
    found (should not happen for a live pool member; the caller has already
    resolved the row).
    """
    now = datetime.now(tz=UTC)
    bands: list[str | None] = []
    applicant_row = (
        await db.execute(
            text("SELECT updated_at FROM applicants WHERE id = :a AND company_id = :c"),
            {"a": applicant_id, "c": company_id},
        )
    ).mappings().first()
    if applicant_row is not None:
        cv_band, _ = freshness_band(applicant_row["updated_at"], now=now)
        bands.append(cv_band)
    evidence_rows = (
        await db.execute(
            text(_EVIDENCE_SQL),
            {"company_id": company_id, "ids": [str(applicant_id)],
             "viewer": viewer_user_id, "cap": _EVIDENCE_ROW_CAP},
        )
    ).mappings().all()
    for ev_row in evidence_rows:
        item = _evidence_why_item(ev_row, applicant_id=str(applicant_id), now=now)
        bands.append(item.get("freshness"))
    return worst_band(bands)


#: Signals that are VERIFIED — produced by somebody other than the candidate,
#: under a process the company controls. Used only by
#: ``verified_evidence_freshness_for_applicant``, never by the display path.
#: ``ai_interview`` is deliberately excluded even though its ``produced_by`` is
#: ``"ai"`` rather than the candidate: this module never reads that row's
#: score (only the FACT that a session happened), so it is not evidence of
#: qualification either — it is already ``unverifiable`` in
#: ``_evidence_why_item`` and would only ever pull a band down, never up, for
#: a reason unrelated to whether the person can do the job.
_VERIFIED_EVIDENCE_SIGNALS = frozenset({"interviewer_scorecard", "round_result", "exam_attempt"})


async def verified_evidence_freshness_for_applicant(
    db: AsyncSession,
    *,
    company_id: uuid.UUID,
    applicant_id: uuid.UUID,
    viewer_user_id: uuid.UUID,
) -> str:
    """The freshness band a CONTROL may trust — answering "is there current,
    VERIFIED evidence of qualification?", which is a different question from
    ``evidence_freshness_for_applicant``'s "how fresh is everything we hold?".

    THE DEFECT THIS EXISTS TO CLOSE (criterion 14, independent acceptance
    pass). ``evidence_freshness_for_applicant`` folds the CV's own band
    (``applicants.updated_at``) into its worst-band computation, for display —
    reasonably, since a stale CV is worth flagging on screen. But a CV's
    upload date is candidate-authored and proves nothing VERIFIED about
    whether the person can do the job: a candidate matched on cosine
    similarity alone, with no scorecard, round result or exam attempt to their
    name, whose CV happens to have been uploaded last week, banded ``fresh``
    on that function alone — clearing ``talent_pools.py::invite_member``'s
    gate with no review and no acknowledgement, which is exactly the overclaim
    design §6.3 says a control must refuse ("an unexplained match cannot be
    invited without the acknowledgement, because there is nothing to
    review").

    So THIS function computes the worst band over VERIFIED evidence only — a
    human interviewer's submitted scorecard (still under the PH4-A1
    independence predicate for *viewer_user_id*), a round result, an exam
    attempt — never the CV, and never the ``ai_interview`` fact (see
    ``_VERIFIED_EVIDENCE_SIGNALS``). Reuses the exact per-row SQL
    (``_EVIDENCE_SQL``) and per-item banding (``_evidence_why_item``) the
    display path uses, filtered to the verified signals, rather than a third
    definition of "how stale is this evidence" — and the same ``worst_band``
    (never the best, never a mean).

    Returns ``"none"`` when there is no verified evidence at all — a
    similarity-only match, or a manually-added candidate with no assessment
    history. ``"none"`` is not ``"fresh"``, so a caller gating on this band
    still requires a current review or an explicit acknowledgement for that
    candidate; that is the correct outcome (design §6.3), and it costs the HR
    manager one recorded click, not a hard block.

    A caller wanting the DISPLAY band — the per-item chips and the row header,
    which legitimately include the CV — must call
    ``evidence_freshness_for_applicant`` instead. The two are expected to
    disagree: a fresh-verified-evidence candidate with a long-stale CV shows a
    stale chip but clears this gate with no acknowledgement, because the CV's
    age is not the question this function answers.
    """
    now = datetime.now(tz=UTC)
    bands: list[str | None] = []
    evidence_rows = (
        await db.execute(
            text(_EVIDENCE_SQL),
            {"company_id": company_id, "ids": [str(applicant_id)],
             "viewer": viewer_user_id, "cap": _EVIDENCE_ROW_CAP},
        )
    ).mappings().all()
    for ev_row in evidence_rows:
        if ev_row["signal"] not in _VERIFIED_EVIDENCE_SIGNALS:
            continue
        item = _evidence_why_item(ev_row, applicant_id=str(applicant_id), now=now)
        bands.append(item.get("freshness"))
    return worst_band(bands)


def _scored_competency_ids(items: list[dict[str, Any]]) -> set[str]:
    """Competency ids this candidate has a VERIFIABLE score against.

    A human scorecard's frozen ``competency_id`` (the composite FK to
    ``round_criteria (round_id, competency_id)`` is what makes that id
    trustworthy) and the KEYS of a round result's ``criterion_scores``. An
    ``unverifiable`` item contributes nothing — a score whose content no longer
    exists is not evidence of a competency.
    """
    out: set[str] = set()
    for item in items:
        if item.get("freshness") == "unverifiable":
            continue
        if item.get("competency_id"):
            out.add(str(item["competency_id"]))
        for cid in item.get("competency_ids") or []:
            out.add(str(cid))
    return out


def _strength(item: dict[str, Any]) -> float:
    """How strong one evidence item is, normalised to 0..1, for picking the
    freshness factor's source. Not a score of the candidate and never shown."""
    if item.get("score") is not None and item.get("of"):
        return float(item["score"]) / float(item["of"])
    if item.get("percent") is not None:
        return float(item["percent"]) / 100.0
    return 0.0


def _assemble_result(
    *,
    base: Any,
    semantic: float,
    lexical: float,
    semantic_available: bool,
    evidence_rows: list[Any],
    target: dict[str, Any] | None,
    now: datetime,
) -> dict[str, Any]:
    """One result row: the score, its breakdown, the ``why`` array, the
    freshness header and the review flags.

    THE SCORE: ``LEAST(1.0, 0.7·semantic + 0.3·lexical + evidence_boost)``.
    The relevance half is the arithmetic ``GET /hr/applicants?q=`` already uses,
    unchanged and with the same weights, so the tuning question stays one
    question. The boost is capped, visible in ``breakdown``, and zero without a
    target opening.

    THE CONTRIBUTIONS are percentage points of the final 0-100 score, so they
    add up to something a person can check against it: the similarity leg is
    ``100 · 0.7 · semantic``, the terms leg ``100 · 0.3 · lexical``, and the
    boost is split evenly across the evidence items that actually covered a
    target competency. An even split is a statement about WHICH items earned
    the boost, not a claim about their relative importance — nothing in the
    data supports a weighting between two competencies the opening asks for
    equally.
    """
    applicant_id = str(base["id"])
    why: list[dict[str, Any]] = []

    cv_band, cv_band_reason = freshness_band(base["updated_at"], now=now)
    semantic_points = round(100 * _SEMANTIC_WEIGHT * semantic)
    if semantic_available and semantic > 0:
        why.append({
            "signal": "resume_similarity",
            "contribution": semantic_points,
            # THE honest line in this whole feature. A semantically similar CV
            # has no matching text to quote, so there is no span and no term —
            # and no sentence a model could write about it would be a fact.
            "explainable": False,
            "note": SIMILARITY_NOTE,
            "locator": f"CV, updated {_locator_date(base['updated_at'])}",
            "recorded_at": base["updated_at"].isoformat() if base["updated_at"] else None,
            "freshness": cv_band,
            "freshness_reason": cv_band_reason,
            "lifecycle": "live",
            "produced_by": "candidate",
            "citation": _citation(
                "applicant", cite_id=applicant_id, route_id=applicant_id, label="CV",
            ),
        })

    terms = list(base["terms_matched"] or [])
    lexical_points = round(100 * _LEXICAL_WEIGHT * lexical)
    if terms:
        why.append({
            "signal": "resume_terms",
            "contribution": lexical_points,
            "explainable": True,
            "terms_matched": terms,
            # CV prose the caller can already read on /hr/applicants/{id}, so it
            # widens nothing — capped, injection-checked, invisible characters
            # stripped, and with bracket markers rather than HTML tags so no
            # consumer needs dangerouslySetInnerHTML.
            "snippet": _safe_snippet(base["snippet"]),
            "locator": f"CV, updated {_locator_date(base['updated_at'])}",
            "recorded_at": base["updated_at"].isoformat() if base["updated_at"] else None,
            "freshness": cv_band,
            "freshness_reason": cv_band_reason,
            "lifecycle": "live",
            "produced_by": "candidate",
            "citation": _citation(
                "applicant", cite_id=applicant_id, route_id=applicant_id, label="CV",
            ),
        })

    evidence_items = [
        _evidence_why_item(row, applicant_id=applicant_id, now=now) for row in evidence_rows
    ]
    scored_ids = _scored_competency_ids(evidence_items)
    target_ids = list(target["competency_ids"]) if target else []
    coverage, covered_ids = competency_coverage(target_ids=target_ids, scored_ids=scored_ids)

    contributing = [
        item for item in evidence_items
        if covered_ids and (
            item.get("competency_id") in covered_ids
            or set(item.get("competency_ids") or []) & set(covered_ids)
        )
    ]
    strongest = max(contributing, key=lambda i: (_strength(i), i["recorded_at"] or ""), default=None)
    factor = _FRESHNESS_FACTOR.get(str(strongest["freshness"]), 0.0) if strongest else 0.0
    boost = evidence_boost(coverage=coverage, factor=factor, has_target=bool(target_ids))
    boost_points = round(100 * boost)
    if contributing and boost_points:
        share = boost_points // len(contributing)
        for item in contributing:
            item["contribution"] = share

    # Newest first, then the ones that earned the boost first — two passes
    # because Python's sort is stable and an ISO date string cannot be negated.
    evidence_items.sort(key=lambda i: str(i["recorded_at"] or ""), reverse=True)
    evidence_items.sort(key=lambda i: -int(i["contribution"]))
    why.extend(evidence_items[:_MAX_WHY_EVIDENCE])

    relevance = _SEMANTIC_WEIGHT * semantic + _LEXICAL_WEIGHT * lexical
    score = min(1.0, relevance + boost)
    # Explained means SOMETHING SAYS WHY THIS MATCHED. A displayed evidence
    # item that did not contribute does not: with no target opening the match
    # was caused by the CV alone, and saying otherwise would dress a cosine
    # number in somebody else's evidence.
    explained = bool(terms) or any(int(i["contribution"]) > 0 for i in evidence_items)
    header_band = worst_band([i.get("freshness") for i in evidence_items])
    reasons = review_reasons(
        header_band=header_band, explained=explained, evidence_items=len(evidence_items),
    )
    return {
        "applicant_id": applicant_id,
        "full_name": base["full_name"],
        "current_title": base["current_title"],
        "current_company": base["current_company"],
        "years_experience": base["years_experience"],
        "score": max(0, round(100 * score)),
        "explained": explained,
        "breakdown": {
            "semantic": round(semantic, 4),
            "lexical": round(lexical, 4),
            "evidence_boost": round(boost, 4),
            "coverage": round(coverage, 4),
            "freshness_factor": factor,
            "covered_competencies": covered_ids,
            "semantic_available": semantic_available,
            "weights": {
                "semantic": _SEMANTIC_WEIGHT,
                "lexical": _LEXICAL_WEIGHT,
                "evidence_max": float(settings.rediscovery_evidence_weight),
            },
        },
        "why": why,
        "evidence_freshness": header_band,
        "requires_review": bool(reasons),
        "review_reasons": reasons,
        "unexplained_note": None if explained else UNEXPLAINED_NOTE,
        "eligibility": {
            "opted_in_at": base["opted_in_at"].isoformat() if base["opted_in_at"] else None,
            "expires_at": (
                base["opt_in_expires_at"].isoformat() if base["opt_in_expires_at"] else None
            ),
        },
    }


async def search(
    db: AsyncSession,
    *,
    company_id: uuid.UUID,
    hr_user_id: uuid.UUID,
    query: str,
    requisition_id: uuid.UUID | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """Rediscovery search over one company's opted-in candidates.

    TWO STATEMENTS, the shape ``_semantic_search`` already uses and a reviewer
    already recognises. The first ranks the eligible universe with the existing
    hybrid arithmetic. The second hydrates the top rows, RE-APPLYING the
    eligibility CTE against those ids and re-binding ``company_id`` as defence
    in depth — so a candidate who becomes ineligible between the two statements
    drops out, because eligibility is re-asserted rather than assumed. The
    evidence join rides on the second, never on the ranking scan.

    GRACEFUL DEGRADATION, said out loud. If the embedding service is down the
    semantic leg becomes the literal ``0``, the signal predicate narrows to
    pure full text, and ``semantic: false`` comes back in the response so the
    screen can say "semantic search is unavailable — matching keywords only"
    rather than quietly returning a worse ranking.

    ``company_id`` and ``hr_user_id`` come from the authenticated session
    (``HrCtxDep``), never from request input. ``hr_user_id`` is also the viewer
    for the PH4-A1 independence predicate, which is why it is required rather
    than optional.
    """
    query = (query or "").strip()
    if not query:
        raise RediscoveryError(422, "empty_query", "Type something to search for.")
    months = int(settings.rediscovery_consent_months)
    page = max(1, min(int(limit or 20), int(settings.rediscovery_evidence_limit)))
    now = datetime.now(tz=UTC)

    target = (
        await _target_opening(db, company_id=company_id, requisition_id=requisition_id)
        if requisition_id is not None
        else None
    )
    universe = await universe_counts(db, company_id=company_id, months=months)

    qvec: list[float] = []
    semantic_available = True
    try:
        qvec = await embed_one_remote(
            text=query, task_type="query", acting_user_id=str(hr_user_id),
        )
    except EmbeddingError as exc:
        # Reported to the caller, not only to the log: the applicant search's
        # silent fallback (hr_applicants.py) is the gap Wave 3 closed for the
        # corpus and this route does not repeat.
        log.warning("rediscovery.search.embed_unavailable", error=str(exc))
        semantic_available = False

    weights = {
        "semantic": _SEMANTIC_WEIGHT,
        "lexical": _LEXICAL_WEIGHT,
        "evidence_max": float(settings.rediscovery_evidence_weight),
    }
    empty: dict[str, Any] = {
        "semantic": semantic_available,
        "universe": universe,
        "matched": 0,
        "returned": 0,
        "weights": weights,
        "target": target,
        "results": [],
    }
    if not universe["eligible"]:
        return empty

    lexical_expr = (
        "ts_rank_cd(to_tsvector('english', coalesce(e.resume_text, '')),"
        " plainto_tsquery('english', :q))"
    )
    params: dict[str, Any] = {
        "company_id": company_id, "months": months, "q": query,
        "limit": int(settings.rediscovery_search_limit),
        "ct": REDISCOVERY_CONSENT_TYPE, "pu": REDISCOVERY_PURPOSE,
    }
    if qvec:
        params["qvec"] = to_pgvector_literal(qvec)
        semantic_expr = (
            "CASE WHEN e.embedding IS NULL THEN 0"
            " ELSE 1 - (e.embedding <=> CAST(:qvec AS halfvec)) END"
        )
        signal_predicate = f"(e.embedding IS NOT NULL OR ({lexical_expr}) > 0)"
    else:
        semantic_expr = "0"
        signal_predicate = f"({lexical_expr}) > 0"
    score_expr = (
        f"({_SEMANTIC_WEIGHT} * ({semantic_expr})"
        f" + {_LEXICAL_WEIGHT} * LEAST(({lexical_expr}), 1.0))"
    )

    # Every fragment concatenated below is a FIXED expression built from this
    # module's own constants and the two hand-written expressions above; the
    # only values that vary per call (:company_id, :months, :q, :qvec, :limit)
    # are bound parameters — as are :ct/:pu, which never vary but are bound
    # rather than spliced in (FIX 3). Tenancy and eligibility are IN this one
    # statement by construction — the app/corpus.py::search_corpus discipline,
    # and the reason the one B608 suppression is anchored to the first line.
    ranked = (
        await db.execute(
            text(
                ELIGIBLE_CTE
                + " SELECT e.id AS id,"  # nosec B608
                f"        ({semantic_expr})::float AS semantic,"
                f"        LEAST(({lexical_expr}), 1.0)::float AS lexical"
                "   FROM eligible e"
                f"  WHERE {signal_predicate}"
                f"  ORDER BY {score_expr} DESC, e.updated_at DESC NULLS LAST"
                "  LIMIT :limit"
            ),
            params,
        )
    ).mappings().all()
    if not ranked:
        return empty

    top = ranked[:page]
    ids = [r["id"] for r in top]
    legs = {r["id"]: (float(r["semantic"]), float(r["lexical"])) for r in top}

    hydrate_params: dict[str, Any] = {
        "company_id": company_id, "months": months, "q": query,
        "ids": [str(i) for i in ids], "words": query_terms(query),
        "hl": _HEADLINE_OPTIONS, "cap": SNIPPET_CHARS,
        "ct": REDISCOVERY_CONSENT_TYPE, "pu": REDISCOVERY_PURPOSE,
    }
    rows = (
        await db.execute(
            text(
                ELIGIBLE_CTE
                # nosec B608: the only interpolation is lexical_expr, this
                # module's own constant expression; :q/:ids/:words/:ct/:pu are
                # bound.
                + " SELECT e.id, e.full_name, e.current_title, e.current_company,"  # nosec B608
                "        e.years_experience, e.updated_at, e.opted_in_at, e.opt_in_expires_at,"
                "        (SELECT coalesce(array_agg(w ORDER BY w), ARRAY[]::text[])"
                "           FROM unnest(CAST(:words AS text[])) AS w"
                "          WHERE to_tsvector('english', coalesce(e.resume_text, ''))"
                "                @@ plainto_tsquery('english', w)) AS terms_matched,"
                f"        CASE WHEN ({lexical_expr}) > 0"
                "              THEN left(ts_headline('english', coalesce(e.resume_text, ''),"
                "                        plainto_tsquery('english', :q), :hl), :cap)"
                "              ELSE NULL END AS snippet"
                "   FROM eligible e"
                "  WHERE e.id = ANY(CAST(:ids AS uuid[]))"
            ),
            hydrate_params,
        )
    ).mappings().all()

    evidence = (
        await db.execute(
            text(_EVIDENCE_SQL),
            {"company_id": company_id, "ids": [str(i) for i in ids],
             "viewer": hr_user_id, "cap": _EVIDENCE_ROW_CAP},
        )
    ).mappings().all()
    by_applicant: dict[str, list[Any]] = {}
    for row in evidence:
        by_applicant.setdefault(str(row["applicant_id"]), []).append(row)

    results = [
        _assemble_result(
            base=row,
            semantic=legs.get(row["id"], (0.0, 0.0))[0],
            lexical=legs.get(row["id"], (0.0, 0.0))[1],
            semantic_available=semantic_available and bool(qvec),
            evidence_rows=by_applicant.get(str(row["id"]), []),
            target=target,
            now=now,
        )
        for row in rows
    ]
    # An unexplained match sorts BELOW an explained one at equal score. It
    # cannot be invited without an acknowledgement either — there is nothing
    # there to review.
    results.sort(key=lambda r: (-int(r["score"]), 0 if r["explained"] else 1))
    return {
        "semantic": semantic_available and bool(qvec),
        "universe": universe,
        # Capped at rediscovery_search_limit: "at least this many matched".
        "matched": len(ranked),
        "returned": len(results),
        "weights": weights,
        "target": target,
        "results": results,
    }


def record_search_audit(
    db: AsyncSession,
    *,
    actor_id: uuid.UUID,
    company_id: uuid.UUID,
    payload: dict[str, Any],
    query_length: int,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> None:
    """Audit one rediscovery read — facts only.

    **The query string is never recorded, here or in any log line.** A
    rediscovery search box is a place HR will type a candidate's name, and an
    audit row is the wrong place for that to end up. Its LENGTH, the result
    count, the target opening and the weights are enough to answer "who
    searched this company's opted-in candidates, when, and how many names came
    back" — which is what the audit exists for. Same rule as
    ``agent.chat.answered`` and ``app/corpus.py::_audit``.

    Staged on the caller's transaction.
    """
    target = payload.get("target") or {}
    db.add(
        AuditLog(
            actor_id=actor_id, actor_type="user", action="rediscovery.searched",
            resource_type="company", resource_id=company_id,
            details={
                "query_length": int(query_length),
                "results": int(payload.get("returned") or 0),
                "matched": int(payload.get("matched") or 0),
                "eligible": int((payload.get("universe") or {}).get("eligible") or 0),
                "semantic": bool(payload.get("semantic")),
                "requisition_id": target.get("requisition_id"),
                "target_competencies": target.get("competencies"),
                "weights": payload.get("weights"),
            },
            ip_address=ip_address, user_agent=user_agent,
            event_ts=datetime.now(tz=UTC),
        )
    )
