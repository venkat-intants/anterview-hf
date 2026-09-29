"""Camera proctoring for exams — event vocabulary, severity scoring, the
"no frame ever" guarantee, and the ``video_capture`` consent gate for the
applicant magic-link (unauthenticated) exam-take flow.

WHAT ALREADY EXISTS (do not rebuild)
``exam_integrity_events`` (``attempt_id``, ``company_id``, ``event_type``,
``started_at``, ``ended_at`` nullable, ``event_metadata`` JSONB — see the "NO
FRAME" section below for why this module's endpoint never writes to that last
column even though it still exists on the row),
``POST /exam/integrity-event`` and the rolling score/summary on
``exam_attempts`` — all in ``routers/exam_take.py``. This module supplies the
pure pieces that endpoint now delegates to, on the same split
``routers/exam_take.py`` already uses for ``app.accommodations`` /
``app.coding_grader`` / ``app.exam_grading``.

EVENT VOCABULARY
Four INSTANTANEOUS browser signals: ``fullscreen_exit``, ``tab_blur``
(unchanged), plus ``copy``/``paste`` — RESTORED here (code review FIX 1,
2026-09-29). Before this branch ``event_type`` was an unconstrained string, so
a client's ``copy``/``paste`` posts were always accepted and stored; this
branch's first pass (migration ``a3c5e7f9b1d4``) tightened the vocabulary to
five names WITHOUT checking what the client already sent, so every
``copy``/``paste`` post started 422-ing — and ``sendIntegrityEvent`` on the
client swallows the error and returns null, so the regression was silent, on
every exam, camera-proctored or not. Three RANGED camera signals:
``face_absent``, ``multiple_faces``, ``gaze_away`` — each carries
``started_at``/``ended_at`` and counts ONCE per debounced occurrence (the
client's ``proctorLogic`` state machine decides when one has occurred, never
per tick). An unknown ``event_type`` is refused, not stored (enforced in the
Pydantic model AND, as a backstop, the DB CHECK
``ck_exam_integrity_events_event_type``, migrations ``a3c5e7f9b1d4`` and
``f6b8d0a2c4e6``). The client (``web/src/pages/exam/useExamProctor.ts``) now
filters its OWN outgoing events against this same seven-name vocabulary
before ever posting, so a future drift between what the client emits and what
this module accepts fails loudly in the browser console rather than as a
silent 422 loss — see that file's ``KNOWN_EVENT_TYPES``.

SEVERITY, NOT A FLAT PENALTY
``score_from_counts`` replaces the old ``100 - 15 × violations``: each event
type has its own weight (``app/config.py``), so a ``multiple_faces`` sighting
costs more than a ``gaze_away`` one instead of counting the same.
``copy``/``paste`` are weighted 5/10 — a paste into an answer brings outside
content IN and is judged a stronger signal than a copy OUT, but neither comes
close to another face on camera (25) or the candidate leaving the frame (20);
``paste`` is deliberately kept below the two browser-integrity signals (15)
because pasting is not, on its own, evidence of leaving the exam environment
the way tabbing away or exiting fullscreen is. ``VIOLATION_EVENT_TYPES`` —
the set that counts toward ``exam_integrity_max_violations`` (the auto-submit
threshold) — deliberately excludes ``gaze_away`` AND ``copy``/``paste``:
before this branch clipboard events were accepted and stored but never
scored and never counted as a violation (the pre-branch ``_VIOLATION_EVENTS``
was exactly ``{fullscreen_exit, tab_blur}``), and a bare copy or paste has too
many innocent explanations on its own — reviewing your own answer, searching
an unfamiliar term from the question text, pasting a candidate ID — to force
an auto-submit unaccompanied by a stronger signal. Restoring them therefore
gives them a severity weight (so they now cost real score, which they never
did before) without silently promoting them to an auto-submit trigger they
never were. ``gaze_away`` stays excluded for the reason already documented
here: it is the least reliable signal and the most likely to penalise someone
for thinking, for a motor or visual difference, or for using assistive
technology, so it is context for a human reading the timeline, never evidence
strong enough to force an auto-submit on its own. This holds for EVERY
candidate, accommodated or not, which is what closes the camera-proctoring
contract's §5 requirement that a relaxation "relax the camera signals too,
not only auto-submit" — ``face_absent``/``multiple_faces`` DO count as
violations, so the existing ``attempt.auto_submit_relaxed`` freeze (PH4-D2,
``exam_take._max_violations_for``) already suppresses auto-submit on them for
a relaxed candidate exactly as it always has for the two browser signals,
with no second relaxation field.

NO FRAME, IMAGE OR LANDMARK ARRAY, EVER
There is no ``metadata`` field on the wire at all (code review FIX 3,
2026-09-29): ``IntegrityEventIn`` declares no such field and, because it sets
``model_config = ConfigDict(extra="forbid")``, posting one is refused outright
as an unrecognised field — never stored, whatever shape it is. This replaces
an earlier, weaker design (a ``metadata`` field guarded by a structural
shape-checker that rejected lists/dicts and oversized strings): that checker
still let through anything that LOOKED like a short, innocuous value — a
name, a phone number, a health detail — and this endpoint is reachable by
anyone holding a valid exam magic link, so "looks innocuous" was never a real
guarantee. Refusing the field ENTIRELY is the guarantee made true by
construction rather than by re-wording it: nothing legitimate ever populated
it (neither ``useExamProctor.ts`` nor the shared ``useProctoring.ts`` camera
module ever set it), so there was no real caller to preserve. The
``exam_integrity_events.event_metadata`` DB column itself is left in place
(rows predating this endpoint's own validation are not this fix's concern,
and dropping a column is a separate, larger migration than removing a field
from a request schema), but this endpoint never writes to it — every INSERT
below omits it, so it is always NULL for every row this endpoint creates from
here on.

THE CONSENT GATE
The camera must not start without an explicit, recorded ``video_capture``
consent for that candidate (DPDP §6(1): consent is freely given or it is not
consent). Exam candidates reach this flow over an opaque magic-link token,
never a login (``routers/exam_take.py``'s module docstring) — so, unlike
``routers/consent.py``'s ``POST /consent`` (which needs ``get_current_user``),
this module resolves or MINTS the applicant's own guest identity the same way
every other unauthenticated candidate door does: ``app/guest_identity.py``'s
``link_or_reuse_guest`` (the ``routers/interview_take.py`` redeem-path
extraction) — never a bespoke mint here, on this project's "one writer"
discipline (``app/rediscovery.py::record_opt_in``'s docstring). The row is
written straight into ``dpdp_consent_ledger`` — no second store — under the
SAME ``consent_type='video_capture'`` / ``purpose='interview'`` pair
``routers/consent.py`` already declares for the authenticated AI-interview
camera consent (``_VIDEO_CONSENT_TYPE`` / ``_PURPOSE_BY_TYPE``), so the two
doors govern one shared consent, and a candidate who has already granted it
for the AI interview does not have to grant it twice for an exam.

RE-GRANT AFTER A WITHDRAWAL. Reachable here with the same exam-round magic
link the applicant was mailed — a private, single-round credential, not an
open form anybody can type an email into (contrast
``public_apply.py::_record_apply_consent``'s "sticky withdrawal", which exists
BECAUSE that door has no such credential). Ticking the box again, on THIS
round's own consent screen, is the owner's own fresh act, exactly the
reasoning ``job_tasks.py::_record_submission_consent`` already uses for the
task-link consent — so a previous withdrawal (through ``DELETE /consent`` or
anywhere else) does not lock this door; it is simply granted again, with a
new ``granted_at``.

DECLINING is never recorded here — DPDP requires evidence of a GRANT, not of
a refusal, and a candidate who declines a REQUIRED round's camera simply never
calls this endpoint and never starts (``exam_take.start_attempt``'s consent
check).
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.guest_identity import link_or_reuse_guest
from app.mailer import candidate_language
from app.models import Applicant

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Event vocabulary
# ---------------------------------------------------------------------------
#: Ranged — carry started_at/ended_at, count once per debounced occurrence.
RANGED_EVENT_TYPES: frozenset[str] = frozenset({"face_absent", "multiple_faces", "gaze_away"})
#: Instantaneous — no ended_at. fullscreen_exit/tab_blur are the original
#: ingest; copy/paste were RESTORED (code review FIX 1, 2026-09-29) — see the
#: module docstring's EVENT VOCABULARY section for why they briefly 422'd.
INSTANT_EVENT_TYPES: frozenset[str] = frozenset(
    {"fullscreen_exit", "tab_blur", "copy", "paste"}
)
KNOWN_EVENT_TYPES: frozenset[str] = RANGED_EVENT_TYPES | INSTANT_EVENT_TYPES
#: The types that need an active camera. Today identical to RANGED_EVENT_TYPES
#: (every camera signal is ranged); kept as its own name because the two ideas
#: — "ranged" and "needs a camera" — are conceptually different and a future
#: instantaneous camera event (e.g. a single snapshot-based check) must gate on
#: this one, not on "ranged".
CAMERA_EVENT_TYPES: frozenset[str] = RANGED_EVENT_TYPES

#: Counts toward exam_integrity_max_violations (the auto-submit threshold).
#: Deliberately NOT gaze_away, and NOT copy/paste either — see the module
#: docstring's SEVERITY section for why a bare clipboard event never forces
#: an auto-submit on its own.
VIOLATION_EVENT_TYPES: frozenset[str] = frozenset(
    {"fullscreen_exit", "tab_blur", "face_absent", "multiple_faces"}
)


# ---------------------------------------------------------------------------
# Severity weights + the rolling score
# ---------------------------------------------------------------------------
def severity_weights() -> dict[str, int]:
    """Per-event-type weight, read from settings so a retune is a config
    change, never a code change."""
    return {
        "multiple_faces": settings.exam_integrity_weight_multiple_faces,
        "face_absent": settings.exam_integrity_weight_face_absent,
        "fullscreen_exit": settings.exam_integrity_weight_fullscreen_exit,
        "tab_blur": settings.exam_integrity_weight_tab_blur,
        "paste": settings.exam_integrity_weight_paste,
        "copy": settings.exam_integrity_weight_copy,
        "gaze_away": settings.exam_integrity_weight_gaze_away,
    }


def score_from_counts(counts: dict[str, int]) -> int:
    """Rolling integrity score: ``max(0, 100 - Σ weight × count)``, replacing
    the old flat ``100 - 15 × violations``. An event type this function does
    not recognise contributes no penalty (defence in depth only — the ingest
    endpoint never stores one)."""
    weights = severity_weights()
    penalty = sum(weights.get(event_type, 0) * n for event_type, n in counts.items())
    return max(0, 100 - penalty)


# ---------------------------------------------------------------------------
# The video_capture consent gate
# ---------------------------------------------------------------------------
#: Same pair routers/consent.py declares for the authenticated AI-interview
#: camera consent (_VIDEO_CONSENT_TYPE / _PURPOSE_BY_TYPE) — one shared
#: consent, not a second type for the exam door.
CONSENT_TYPE = "video_capture"
CONSENT_PURPOSE = "interview"
#: Bumped when the candidate-facing camera notice's wording changes materially
#: (the job_tasks.py::CONSENT_NOTICE_VERSION precedent).
CONSENT_NOTICE_VERSION = "1"

#: The candidate's currently-active camera grant, if any. Used twice by
#: ``record_camera_consent`` — as the idempotency fast path, and again to
#: resolve the S4-009 double-grant race — so the two can never drift into
#: asking subtly different questions. Binds :uid only; the type/purpose pair is
#: fixed for this module.
_ACTIVE_GRANT_SQL = text(
    "SELECT id, granted_at FROM dpdp_consent_ledger"
    " WHERE user_id = :uid AND consent_type = :ct AND purpose = :pu"
    "   AND granted AND revoked_at IS NULL"
    " ORDER BY granted_at DESC LIMIT 1"
).bindparams(ct=CONSENT_TYPE, pu=CONSENT_PURPOSE)


@dataclass(frozen=True)
class CameraConsentMeta:
    """Hashed-at-the-edge request metadata for the ledger row. Raw IP/UA are
    hashed here and never stored — dpdp_consent_ledger.evidence never carries
    raw PII (models.DpdpConsent's contract, reproduced rather than imported
    the same way app/rediscovery.py reproduces routers/consent.py's helper so
    this module does not depend on a router)."""

    ip_address: str | None = None
    user_agent: str | None = None


def _hash_value(raw: str) -> str:
    return hashlib.sha256((raw + settings.consent_ip_salt).encode("utf-8")).hexdigest()


async def has_active_camera_consent(db: AsyncSession, user_id: uuid.UUID | None) -> bool:
    """Whether *user_id* holds an active (granted, not revoked) video_capture
    consent. ``user_id is None`` (an applicant with no identity of their own
    yet — nobody has ever recorded consent for them) is always False, never a
    query — there is nothing to find."""
    if user_id is None:
        return False
    found = await db.scalar(
        text(
            "SELECT 1 FROM dpdp_consent_ledger"
            " WHERE user_id = :uid AND consent_type = :ct AND purpose = :pu"
            "   AND granted AND revoked_at IS NULL"
            " LIMIT 1"
        ),
        {"uid": user_id, "ct": CONSENT_TYPE, "pu": CONSENT_PURPOSE},
    )
    return found is not None


async def record_camera_consent(
    db: AsyncSession,
    *,
    applicant: Applicant,
    meta: CameraConsentMeta,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Record this applicant's own, standalone consent to the camera for the
    round they are about to take. Idempotent: a repeat call while a grant is
    already active returns it unchanged rather than minting a second row.

    Provisions the applicant's guest identity first when they have none yet
    (``app/guest_identity.py::link_or_reuse_guest`` — the ``interview_take``
    precedent), because ``dpdp_consent_ledger.user_id`` is NOT NULL: consent
    has to hang off a user even for a candidate who has never logged in.

    Returns ``{"consented": True, "already_granted": bool, "granted_at": iso}``.
    Staged on the caller's transaction; the caller commits.
    """
    now = now or datetime.now(tz=UTC)
    user_id = applicant.user_id
    if user_id is None:
        user_id = await link_or_reuse_guest(
            db,
            applicant_id=applicant.id,
            full_name=applicant.full_name,
            company_id=applicant.company_id,
            language=await candidate_language(db, applicant.id),
            email_prefix="exam",
            now=now,
        )
    if user_id is None:  # pragma: no cover - applicant vanished mid-request
        raise ValueError("applicant has no identity to record consent against")

    existing = await db.execute(_ACTIVE_GRANT_SQL, {"uid": user_id})
    row = existing.mappings().first()
    if row is not None:
        log.info("exam.camera_consent.idempotent", applicant_id=str(applicant.id))
        return {
            "consented": True,
            "already_granted": True,
            "granted_at": row["granted_at"].isoformat(),
        }

    consent_id = uuid.uuid4()
    evidence = {
        "source": "exam_camera_consent",
        "applicant_id": str(applicant.id),
        "company_id": str(applicant.company_id),
        "notice_version": CONSENT_NOTICE_VERSION,
        "ip_hash": _hash_value(meta.ip_address or ""),
        "ua_hash": _hash_value(meta.user_agent or ""),
        "consented_at_iso": now.isoformat(),
    }
    try:
        # A SAVEPOINT, not a bare INSERT. This function stages work on the
        # CALLER's transaction (``/exam/camera-consent`` commits it), so an
        # IntegrityError escaping here would poison that transaction and turn a
        # double-click on "Start exam" into a failed start — the opposite of
        # the idempotency this docstring promises. begin_nested() confines the
        # rollback to the failed INSERT and leaves the outer transaction usable.
        async with db.begin_nested():
            await db.execute(
                text(
                    "INSERT INTO dpdp_consent_ledger"
                    " (id, user_id, consent_type, granted, granted_at, purpose, evidence)"
                    " VALUES (:id, :uid, :ct, true, :n, :pu, CAST(:ev AS jsonb))"
                ),
                {
                    "id": consent_id, "uid": user_id, "ct": CONSENT_TYPE, "n": now,
                    "pu": CONSENT_PURPOSE, "ev": json.dumps(evidence),
                },
            )
    except IntegrityError:
        # S4-009, the same race ``routers/consent.py::record_consent`` handles
        # and the reason this module reuses its ledger rather than a second
        # store: the partial unique index ``ix_dpdp_consent_active_unique``
        # permits ONE active grant per (user_id, consent_type, purpose), so a
        # concurrent request that also got past the SELECT above won this
        # INSERT. Its row is exactly the grant this candidate asked for, so
        # report it the same way the idempotent fast path does.
        raced = await db.execute(_ACTIVE_GRANT_SQL, {"uid": user_id})
        won = raced.mappings().first()
        if won is None:  # pragma: no cover - the index tripped for some other reason
            raise
        log.info("exam.camera_consent.raced", applicant_id=str(applicant.id))
        return {
            "consented": True,
            "already_granted": True,
            "granted_at": won["granted_at"].isoformat(),
        }
    log.info(
        "exam.camera_consent.recorded", applicant_id=str(applicant.id),
        company_id=str(applicant.company_id),
    )
    return {"consented": True, "already_granted": False, "granted_at": now.isoformat()}
