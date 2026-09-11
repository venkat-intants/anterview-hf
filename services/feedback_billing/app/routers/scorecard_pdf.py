"""POST /internal/scorecards/{scorecard_id}/pdf — render a missing scorecard PDF (A1).

Why this exists
---------------
The PDF is rendered by a fire-and-forget task at the end of ``score_session``.
That is the right shape for the live path — a candidate who has just finished
must not wait on ReportLab and an S3 upload before seeing their scores — but it
has no second half. If the task fails (storage down, a render error, the process
restarting mid-upload) it logs and the scorecard stays without a PDF for good.

This endpoint is the second half. data_gateway's reconciler finds scorecards
whose ``report_pdf_key`` is still NULL and calls it; everything the PDF needs is
already on the scorecard row, so no LLM call and no transcript is involved.

Idempotent
----------
* A scorecard that already has a key is reported ``already_present`` and left
  alone — including when the live task finishes while this one is rendering.
* The key write is ``WHERE report_pdf_key IS NULL``, so two renders can never
  disagree about which object the row points at. They upload to the same key.

Erasure
-------
Retention and erasure both delete the scorecard row after deleting its objects.
A render that races one of them would upload a PDF — carrying the candidate's
name — after the objects were collected, leaving it orphaned in the bucket with
no row pointing at it. So a render whose row is gone by the time it tries to
record the key deletes what it uploaded. Sessions and users already pending
erasure are refused up front.
"""

from __future__ import annotations

import contextlib
import json
import uuid
from typing import Annotated, Any, Literal

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.config import settings as _app_settings
from app.database import get_db_session
from app.pdf_render import render_scorecard_pdf
from app.routers.score import _require_service_jwt

log = structlog.get_logger(__name__)

router = APIRouter(tags=["internal"])


def _get_settings() -> Settings:
    return _app_settings


class PdfRenderResponse(BaseModel):
    scorecard_id: str
    status: Literal["rendered", "already_present", "not_applicable"]
    # Why a scorecard will never get a PDF. The reconciler parks these instead
    # of retrying, because nothing about a later attempt would be different.
    reason: str | None = None


_ROW_SQL = """
SELECT sc.scorecard_id, sc.session_id, sc.scores, sc.composite_score,
       sc.strengths, sc.improvements, sc.summary, sc.lang, sc.report_pdf_key,
       s.deleted_at AS session_deleted_at,
       u.full_name, u.deleted_at AS user_deleted_at,
       j.title AS job_title
  FROM scorecards sc
  LEFT JOIN sessions s ON s.id = sc.session_id
  LEFT JOIN users    u ON u.id = s.user_id
  LEFT JOIN jobs     j ON j.id = s.job_id
 WHERE sc.scorecard_id = :id
"""


def _json(val: Any, default: Any) -> Any:
    if val is None:
        return default
    if isinstance(val, str):
        return json.loads(val)
    return val


def _improvements(raw: list[Any]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for item in raw:
        if isinstance(item, dict):
            out.append({"area": str(item.get("area", "")),
                        "suggestion": str(item.get("suggestion", ""))})
        else:
            out.append({"area": "", "suggestion": str(item)})
    return out


async def _delete_object(key: str, settings: Settings) -> None:
    from shared.s3 import s3_client  # noqa: PLC0415 — see pdf_render._upload_to_s3

    async with s3_client(
        endpoint=settings.s3_endpoint_url,
        region=settings.s3_region,
        access_key=settings.s3_access_key_id,
        secret_key=settings.s3_secret_access_key,
    ) as s3:
        await s3.delete_object(Bucket=settings.s3_scorecard_bucket, Key=key)


@router.post(
    "/scorecards/{scorecard_id}/pdf",
    response_model=PdfRenderResponse,
    summary="Render and store a scorecard's missing PDF (idempotent)",
)
async def internal_render_scorecard_pdf(
    scorecard_id: uuid.UUID,
    _jwt_payload: Annotated[dict[str, Any], Depends(_require_service_jwt)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    app_settings: Annotated[Settings, Depends(_get_settings)],
) -> PdfRenderResponse:
    """Returns 200 with a status, 404 for an unknown scorecard, 503 when storage
    is not configured, 502 when the render or upload fails (worth retrying)."""
    sid = str(scorecard_id)
    row = (await db.execute(sa_text(_ROW_SQL), {"id": scorecard_id})).mappings().first()
    # Release the connection before the CPU-bound render and the upload — the
    # same idle-in-transaction hazard routers/score.py documents.
    with contextlib.suppress(Exception):
        await db.rollback()

    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Scorecard not found.")
    if row["report_pdf_key"]:
        return PdfRenderResponse(scorecard_id=sid, status="already_present")
    if row["session_deleted_at"] is not None or row["user_deleted_at"] is not None:
        return PdfRenderResponse(scorecard_id=sid, status="not_applicable",
                                 reason="pending_erasure")
    name = str(row["full_name"] or "").strip()
    if not name:
        # The live path makes the same call (score_session renders only with a
        # name): the PDF's header is the candidate's name.
        return PdfRenderResponse(scorecard_id=sid, status="not_applicable",
                                 reason="no_candidate_name")
    if not app_settings.s3_access_key_id:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="Scorecard storage is not configured.")

    scores = {k: int(v) for k, v in _json(row["scores"], {}).items()}
    key = await render_scorecard_pdf(
        sid,
        str(row["session_id"]),
        name,
        str(row["job_title"] or ""),
        str(row["lang"] or "en"),
        scores,
        float(row["composite_score"] or 0.0),
        [str(s) for s in _json(row["strengths"], [])],
        _improvements(_json(row["improvements"], [])),
        str(row["summary"] or ""),
        settings=app_settings,
        db_session_factory=None,  # the key is written below, where a failure is visible
    )
    if key is None:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY,
                            detail="Scorecard PDF render or upload failed.")

    recorded = (await db.execute(
        sa_text(
            "UPDATE scorecards SET report_pdf_key = :key"
            " WHERE scorecard_id = :id AND report_pdf_key IS NULL"
            " RETURNING scorecard_id"
        ),
        {"key": key, "id": scorecard_id},
    )).first()
    if recorded is not None:
        await db.commit()
        log.info("scorecard_pdf.rendered", scorecard_id=sid)
        return PdfRenderResponse(scorecard_id=sid, status="rendered")

    still_there = await db.scalar(
        sa_text("SELECT 1 FROM scorecards WHERE scorecard_id = :id"), {"id": scorecard_id}
    )
    await db.rollback()
    if still_there:
        # The live task recorded its key while this one rendered. Same object
        # key, so the upload was a harmless overwrite of an identical file.
        return PdfRenderResponse(scorecard_id=sid, status="already_present")

    # The row was deleted (retention or erasure) while this rendered. Remove
    # what was just uploaded, or it outlives the erasure that collected the rest.
    try:
        await _delete_object(key, app_settings)
    except Exception as exc:  # noqa: BLE001
        log.error("scorecard_pdf.orphan_delete_failed", scorecard_id=sid,
                  error_type=type(exc).__name__)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY,
                            detail="Scorecard was deleted; uploaded PDF could not be removed.") from exc
    log.warning("scorecard_pdf.row_deleted_during_render", scorecard_id=sid)
    return PdfRenderResponse(scorecard_id=sid, status="not_applicable", reason="scorecard_deleted")
