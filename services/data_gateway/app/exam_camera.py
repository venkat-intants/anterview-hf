"""Camera proctoring for exams — event vocabulary, severity scoring, the
"no frame ever" guarantee, and the ``video_capture`` consent gate for the
applicant magic-link (unauthenticated) exam-take flow.

WHAT ALREADY EXISTS (do not rebuild)
``exam_integrity_events`` (``attempt_id``, ``company_id``, ``event_type``,
``started_at``, ``ended_at`` nullable, ``event_metadata`` JSONB),
``POST /exam/integrity-event`` and the rolling score/summary on
``exam_attempts`` — all in ``routers/exam_take.py``. This module supplies the
pure pieces that endpoint now delegates to, on the same split
``routers/exam_take.py`` already uses for ``app.accommodations`` /
``app.coding_grader`` / ``app.exam_grading``.

EVENT VOCABULARY
Two INSTANTANEOUS browser signals (unchanged): ``fullscreen_exit``,
``tab_blur``. Three RANGED camera signals: ``face_absent``,
``multiple_faces``, ``gaze_away`` — each carries ``started_at``/``ended_at``
and counts ONCE per debounced occurrence (the client's ``proctorLogic`` state
machine decides when one has occurred, never per tick). An unknown
``event_type`` is refused, not stored (enforced in the Pydantic model AND, as
a backstop, the DB CHECK ``ck_exam_integrity_events_event_type``, migration
``a3c5e7f9b1d4``).

SEVERITY, NOT A FLAT PENALTY
``score_from_counts`` replaces the old ``100 - 15 × violations``: each event
type has its own weight (``app/config.py``), so a ``multiple_faces`` sighting
costs more than a ``gaze_away`` one instead of counting the same.
``VIOLATION_EVENT_TYPES`` — the set that counts toward
``exam_integrity_max_violations`` (the auto-submit threshold) — deliberately
excludes ``gaze_away``: it is the least reliable signal and the most likely to
penalise someone for thinking, for a motor or visual difference, or for using
assistive technology, so it is context for a human reading the timeline, never
evidence strong enough to force an auto-submit on its own. This holds for
EVERY candidate, accommodated or not, which is what closes the camera-proctoring
contract's §5 requirement that a relaxation "relax the camera signals too, not
only auto-submit" — ``face_absent``/``multiple_faces`` DO count as violations,
so the existing ``attempt.auto_submit_relaxed`` freeze (PH4-D2,
``exam_take._max_violations_for``) already suppresses auto-submit on them for
a relaxed candidate exactly as it always has for the two browser signals, with
no second relaxation field.

NO FRAME, IMAGE OR LANDMARK ARRAY, EVER
``check_metadata_shape`` is structural, not a keyword denylist: no metadata
value may be a list or a dict (a landmark array IS a list), every string is
capped well below what a base64 frame would need, and the whole payload is
capped in bytes. Getting this wrong would create a biometric dataset with
erasure, retention and residency obligations none of the rest of this design
carries.

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
#: Instantaneous — no ended_at, the original two-type ingest.
INSTANT_EVENT_TYPES: frozenset[str] = frozenset({"fullscreen_exit", "tab_blur"})
KNOWN_EVENT_TYPES: frozenset[str] = RANGED_EVENT_TYPES | INSTANT_EVENT_TYPES
#: The types that need an active camera. Today identical to RANGED_EVENT_TYPES
#: (every camera signal is ranged); kept as its own name because the two ideas
#: — "ranged" and "needs a camera" — are conceptually different and a future
#: instantaneous camera event (e.g. a single snapshot-based check) must gate on
#: this one, not on "ranged".
CAMERA_EVENT_TYPES: frozenset[str] = RANGED_EVENT_TYPES

#: Counts toward exam_integrity_max_violations (the auto-submit threshold).
#: Deliberately NOT gaze_away — see the module docstring.
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
# No frame, image or landmark array — ever
# ---------------------------------------------------------------------------
METADATA_MAX_KEYS = 20
METADATA_MAX_BYTES = 2_000
METADATA_MAX_STRING_LEN = 500


def check_metadata_shape(metadata: dict[str, Any] | None) -> dict[str, Any] | None:
    """Refuse anything shaped like a frame, an image or a landmark array.

    Structural, not a keyword denylist: no value may be a list or a dict (a
    landmark array IS a list of numbers; a frame or image needs one or the
    other to arrive as JSON at all), every string is capped well below what a
    base64-encoded frame would need, and the whole payload is capped in
    bytes. A client with nothing to hide sends something like
    ``{"confidence": 0.82}``; this leaves that untouched.

    Raises ``ValueError`` (surfaced by the Pydantic field validator that calls
    this as a 422) rather than silently dropping the offending value — a
    client that thinks it sent proctoring context and had it silently
    discarded is a worse failure mode than a loud rejection.
    """
    if metadata is None:
        return None
    if len(metadata) > METADATA_MAX_KEYS:
        raise ValueError("metadata has too many keys")
    for key, value in metadata.items():
        if isinstance(value, list | dict):
            raise ValueError(
                f"metadata.{key}: arrays and objects are not accepted — no frame, "
                "image or landmark data may ever be sent"
            )
        if isinstance(value, str) and len(value) > METADATA_MAX_STRING_LEN:
            raise ValueError(f"metadata.{key} is too long")
    encoded = json.dumps(metadata, default=str)
    if len(encoded.encode("utf-8")) > METADATA_MAX_BYTES:
        raise ValueError(
            "metadata payload is too large — no frame, image or landmark data may "
            "ever be sent"
        )
    return metadata


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

    existing = await db.execute(
        text(
            "SELECT id, granted_at FROM dpdp_consent_ledger"
            " WHERE user_id = :uid AND consent_type = :ct AND purpose = :pu"
            "   AND granted AND revoked_at IS NULL"
            " ORDER BY granted_at DESC LIMIT 1"
        ),
        {"uid": user_id, "ct": CONSENT_TYPE, "pu": CONSENT_PURPOSE},
    )
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
    await db.execute(
        text(
            "INSERT INTO dpdp_consent_ledger"
            " (id, user_id, consent_type, granted, granted_at, purpose, evidence)"
            " VALUES (:id, :uid, :ct, true, :n, :pu, CAST(:ev AS jsonb))"
        ),
        {
            "id": consent_id, "uid": user_id, "ct": CONSENT_TYPE, "n": now, "pu": CONSENT_PURPOSE,
            "ev": json.dumps(evidence),
        },
    )
    log.info(
        "exam.camera_consent.recorded", applicant_id=str(applicant.id),
        company_id=str(applicant.company_id),
    )
    return {"consented": True, "already_granted": False, "granted_at": now.isoformat()}
