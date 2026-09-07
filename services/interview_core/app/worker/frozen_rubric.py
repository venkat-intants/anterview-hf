"""Load a workflow round's frozen rubric for a live interview — Group C, C8.

Why the worker needs this
-------------------------
``_derive_role_profile`` builds a role model from the job title and JD, calling
Gemini to refine it. That is right for a self-serve practice interview, where
there is no workflow and nothing was promised in advance.

It is wrong for a workflow round. A published workflow froze exactly what that
round assesses — competencies, weights, behavioural anchors and probe stems — at
authoring time. Deriving a *fresh* profile at interview time would quietly
substitute a different rubric: the same competency ids, possibly, but refined
anchors, different weights, and a plan probing things the round never claimed to
measure. The candidate would be interviewed against one standard and the
employer would believe they were interviewed against another.

So when a session belongs to a workflow round, the rubric is read back from the
round rather than derived. No Gemini call, no cache, no drift.

The lookup
----------
    sessions.id
      -> interview_invites.session_id      (which invite produced this session)
      -> enrolments.enrolment_id           (which application)
      -> enrolments.current_round_id       (which round they are sitting)
      -> round_criteria                    (the frozen rubric)

Raw SQL rather than the ORM because the Group C tables belong to
``data_gateway``'s models and are not mapped in this service. Importing another
service's models to read four columns would couple the two images together for
no benefit; the query is stable and lives here, next to the code that needs it.

Never raises. Every failure path returns None and the caller derives a profile
as before, because a missing rubric must degrade the interview's precision, not
prevent the interview.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from shared.intelligence import FrozenRubricError, profile_from_round_criteria
from shared.intelligence.schema import RoleProfile
from sqlalchemy import text

logger = logging.getLogger(__name__)

# One statement, walking invite -> enrolment -> round -> criteria. Ordered by
# weight so a truncated rubric (more criteria than the schema allows) keeps the
# heaviest, matching what profile_from_round_criteria would choose anyway.
_LOOKUP_SQL = """
SELECT wr.id            AS round_id,
       wr.workflow_id   AS workflow_id,
       wr.title         AS round_title,
       wr.kind          AS round_kind,
       rc.competency_id, rc.competency_name, rc.competency_kind,
       rc.weight, rc.anchors, rc.probes
  FROM interview_invites ii
  JOIN enrolments      e  ON e.id  = ii.enrolment_id AND e.deleted_at IS NULL
  JOIN workflow_rounds wr ON wr.id = e.current_round_id AND wr.deleted_at IS NULL
  LEFT JOIN round_criteria rc ON rc.round_id = wr.id
 WHERE ii.session_id = :sid
   AND ii.deleted_at IS NULL
 ORDER BY rc.weight DESC NULLS LAST
"""


async def load_frozen_rubric(
    session_factory: Any, session_id: str | uuid.UUID, *, job_title: str
) -> RoleProfile | None:
    """The rubric this session's round was published with, or None.

    None means "no workflow round applies here" — a practice interview, a
    manually created invite, a round with too few stored criteria — and the
    caller should derive a profile as it always has.
    """
    try:
        sid = session_id if isinstance(session_id, uuid.UUID) else uuid.UUID(str(session_id))
    except (ValueError, AttributeError):
        return None

    try:
        async with session_factory() as db:
            rows = (await db.execute(text(_LOOKUP_SQL), {"sid": sid})).mappings().all()
    except Exception as exc:  # noqa: BLE001 — a rubric lookup must never stop an interview
        logger.warning(
            "interview-worker.frozen_rubric.lookup_failed session=%s err=%s",
            sid, type(exc).__name__,
        )
        return None

    if not rows:
        return None

    head = rows[0]
    criteria = [
        {
            "competency_id": r["competency_id"],
            "competency_name": r["competency_name"],
            "competency_kind": r["competency_kind"],
            "weight": float(r["weight"]) if r["weight"] is not None else 0.0,
            "anchors": r["anchors"],
            "probes": r["probes"],
        }
        for r in rows
        if r["competency_id"]
    ]
    if not criteria:
        # The session belongs to a round, but nobody selected any competencies
        # for it. Worth a log line: the workflow validator blocks this for AI
        # interview rounds, so reaching it means the round changed shape after
        # publication or the data was edited directly.
        logger.info(
            "interview-worker.frozen_rubric.round_has_no_criteria session=%s round=%s",
            sid, head["round_id"],
        )
        return None

    try:
        profile = profile_from_round_criteria(
            criteria,
            job_title=job_title,
            round_title=head["round_title"] or "Interview",
            workflow_id=str(head["workflow_id"]),
            round_id=str(head["round_id"]),
        )
    except FrozenRubricError as exc:
        # Too few criteria to form a rubric. Falling back to the whole role
        # model is better than interviewing against a distorted one.
        logger.info(
            "interview-worker.frozen_rubric.unusable session=%s reason=%s", sid, exc
        )
        return None

    logger.info(
        "interview-worker.frozen_rubric session=%s round=%r kind=%s competencies=%d "
        "profile_id=%s",
        sid, head["round_title"], head["round_kind"], len(profile.competencies),
        profile.profile_id,
    )
    return profile
