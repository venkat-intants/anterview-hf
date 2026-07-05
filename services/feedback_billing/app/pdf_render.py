"""PDF scorecard generator — S5-007.

Builds an HTML scorecard, renders it to PDF bytes via ReportLab, and uploads
the result to S3 (Cloudflare R2-compatible) via aioboto3.

Design decisions:
  - ReportLab chosen over WeasyPrint: WeasyPrint requires native Cairo/Pango
    libraries that are unavailable in the Railway deployment environment.
  - Template is a plain Python f-string (no Jinja2) — the PDF template is
    simple enough that extra templating machinery is unnecessary overhead.
  - Failure is non-raising: any exception is logged and None is returned so
    that the main scoring flow is never disrupted.
  - The S3 upload is fire-and-forget from scorer.py; this module does the
    actual work and also updates scorecards.report_pdf_key on success.

PII rules:
  - The PDF itself contains candidate_name which is personal data.
  - The S3 key (scorecards/{scorecard_id}/report.pdf) must never be logged
    with the candidate_name inline.
  - Access to the S3 object is controlled via pre-signed URLs only.
"""

from __future__ import annotations

import asyncio
import io
from datetime import date
from functools import partial
from typing import Any

import structlog
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    HRFlowable,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from app.config import Settings

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SCORECARD_PREFIX = "scorecards"
_INTANTS_BLUE = colors.HexColor("#4F46E5")  # indigo-600
_INTANTS_LIGHT = colors.HexColor("#EEF2FF")  # indigo-50
_DARK = colors.HexColor("#111827")           # gray-900
_MID = colors.HexColor("#374151")            # gray-700
_MUTED = colors.HexColor("#6B7280")          # gray-500

# Axis human-readable labels (ordered as displayed).
_AXIS_LABELS: dict[str, str] = {
    "communication": "Communication",
    "technical": "Technical Knowledge",
    "problem_solving": "Problem Solving",
    "confidence": "Confidence",
}


# ---------------------------------------------------------------------------
# ReportLab PDF builder
# ---------------------------------------------------------------------------


def _build_pdf_bytes(
    *,
    scorecard_id: str,
    candidate_name: str,
    job_title: str,
    language: str,
    scores: dict[str, int],
    composite_score: float,
    strengths: list[str],
    improvements: list[dict[str, str]],
    summary: str,
) -> bytes:
    """Build and return PDF bytes for the scorecard using ReportLab Platypus."""
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
        title=f"Interview Scorecard — {candidate_name}",
        author="Intants AI Interview Platform",
    )

    base_styles = getSampleStyleSheet()
    story: list[Any] = []

    # ---- Styles -----------------------------------------------------------
    header_style = ParagraphStyle(
        "IntantsHeader",
        parent=base_styles["Normal"],
        fontSize=22,
        leading=28,
        textColor=_INTANTS_BLUE,
        fontName="Helvetica-Bold",
        spaceAfter=2,
    )
    subheader_style = ParagraphStyle(
        "IntantsSubheader",
        parent=base_styles["Normal"],
        fontSize=11,
        leading=14,
        textColor=_MUTED,
        spaceAfter=2,
    )
    section_title_style = ParagraphStyle(
        "SectionTitle",
        parent=base_styles["Normal"],
        fontSize=12,
        leading=16,
        textColor=_INTANTS_BLUE,
        fontName="Helvetica-Bold",
        spaceBefore=10,
        spaceAfter=4,
    )
    body_style = ParagraphStyle(
        "IntantsBody",
        parent=base_styles["Normal"],
        fontSize=10,
        leading=15,
        textColor=_MID,
        spaceAfter=3,
    )
    bullet_style = ParagraphStyle(
        "IntantsBullet",
        parent=body_style,
        leftIndent=14,
        bulletIndent=0,
        spaceAfter=2,
    )
    score_large_style = ParagraphStyle(
        "ScoreLarge",
        parent=base_styles["Normal"],
        fontSize=40,
        leading=48,
        textColor=_INTANTS_BLUE,
        fontName="Helvetica-Bold",
        alignment=1,  # center
    )
    score_denom_style = ParagraphStyle(
        "ScoreDenom",
        parent=base_styles["Normal"],
        fontSize=13,
        leading=16,
        textColor=_MUTED,
        alignment=1,
    )
    footer_style = ParagraphStyle(
        "IntantsFooter",
        parent=base_styles["Normal"],
        fontSize=8,
        leading=10,
        textColor=_MUTED,
        alignment=1,
        spaceBefore=12,
    )

    today = date.today().strftime("%d %B %Y")
    lang_display = {"en": "English", "hi": "Hindi (Hinglish)", "te": "Telugu (Tenglish)"}.get(
        language, language.upper()
    )

    # ---- Header block -------------------------------------------------------
    story.append(Paragraph("Intants AI Interview Platform", header_style))
    story.append(Paragraph("APSSDC Skilling Initiative", subheader_style))
    story.append(HRFlowable(width="100%", thickness=1.5, color=_INTANTS_BLUE, spaceAfter=8))

    # Candidate info table
    info_data = [
        [
            Paragraph("<b>Candidate</b>", body_style),
            Paragraph(candidate_name, body_style),
            Paragraph("<b>Date</b>", body_style),
            Paragraph(today, body_style),
        ],
        [
            Paragraph("<b>Position</b>", body_style),
            Paragraph(job_title, body_style),
            Paragraph("<b>Language</b>", body_style),
            Paragraph(lang_display, body_style),
        ],
    ]
    info_table = Table(info_data, colWidths=["18%", "32%", "18%", "32%"])
    info_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), _INTANTS_LIGHT),
                ("ROWBACKGROUNDS", (0, 0), (-1, -1), [_INTANTS_LIGHT, colors.white]),
                ("BOX", (0, 0), (-1, -1), 0.5, _INTANTS_BLUE),
                ("INNERGRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#C7D2FE")),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    story.append(info_table)
    story.append(Spacer(1, 10))

    # ---- Composite score ----------------------------------------------------
    story.append(Paragraph("Overall Score", section_title_style))
    story.append(Paragraph(f"{composite_score:.1f}", score_large_style))
    story.append(Paragraph("/ 10", score_denom_style))
    story.append(Spacer(1, 8))

    # ---- Score breakdown table ----------------------------------------------
    story.append(Paragraph("Score Breakdown", section_title_style))

    breakdown_header = [
        Paragraph("<b>Dimension</b>", body_style),
        Paragraph("<b>Score</b>", body_style),
        Paragraph("<b>Rating</b>", body_style),
    ]
    breakdown_rows: list[list[Any]] = [breakdown_header]
    for axis_key, axis_label in _AXIS_LABELS.items():
        raw = scores.get(axis_key, 0)
        if raw <= 3:
            rating = "Needs Improvement"
        elif raw <= 5:
            rating = "Below Expectations"
        elif raw <= 7:
            rating = "Meets Expectations"
        elif raw <= 9:
            rating = "Exceeds Expectations"
        else:
            rating = "Exceptional"
        breakdown_rows.append(
            [
                Paragraph(axis_label, body_style),
                Paragraph(str(raw), body_style),
                Paragraph(rating, body_style),
            ]
        )

    breakdown_table = Table(breakdown_rows, colWidths=["50%", "15%", "35%"])
    breakdown_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), _INTANTS_BLUE),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, 0), 10),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, _INTANTS_LIGHT]),
                ("BOX", (0, 0), (-1, -1), 0.5, _INTANTS_BLUE),
                ("INNERGRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#C7D2FE")),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    story.append(breakdown_table)
    story.append(Spacer(1, 8))

    # ---- Strengths ----------------------------------------------------------
    story.append(Paragraph("Key Strengths", section_title_style))
    for strength in strengths[:3]:
        story.append(
            Paragraph(f"&#x2022; &nbsp;{strength}", bullet_style)
        )
    story.append(Spacer(1, 6))

    # ---- Improvements -------------------------------------------------------
    story.append(Paragraph("Areas for Improvement", section_title_style))
    for item in improvements[:3]:
        area = item.get("area", "")
        suggestion = item.get("suggestion", "")
        story.append(
            Paragraph(f"&#x2022; &nbsp;<b>{area}:</b> {suggestion}", bullet_style)
        )
    story.append(Spacer(1, 6))

    # ---- Summary ------------------------------------------------------------
    story.append(Paragraph("Summary", section_title_style))
    story.append(Paragraph(summary, body_style))
    story.append(Spacer(1, 10))

    # ---- Footer -------------------------------------------------------------
    story.append(HRFlowable(width="100%", thickness=0.5, color=_MUTED))
    story.append(
        Paragraph(
            f"Powered by Intants AI &nbsp;|&nbsp; Scorecard ID: {scorecard_id}",
            footer_style,
        )
    )

    doc.build(story)
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# S3 upload helper
# ---------------------------------------------------------------------------


async def _upload_to_s3(
    *,
    pdf_bytes: bytes,
    s3_key: str,
    settings: Settings,
) -> None:
    """Upload PDF bytes to S3 / Cloudflare R2.

    Raises any exception encountered so the caller can catch and log.
    """
    import aioboto3  # local import — optional dep at module level  # noqa: PLC0415

    session = aioboto3.Session(
        aws_access_key_id=settings.s3_access_key_id,
        aws_secret_access_key=settings.s3_secret_access_key,
        region_name=settings.s3_region,
    )
    endpoint = settings.s3_endpoint_url or None

    async with session.client(
        "s3",
        endpoint_url=endpoint,
    ) as s3:
        await s3.put_object(
            Bucket=settings.s3_scorecard_bucket,
            Key=s3_key,
            Body=pdf_bytes,
            ContentType="application/pdf",
            ContentDisposition="inline",
        )


# ---------------------------------------------------------------------------
# DB key update helper (opened from fire-and-forget task)
# ---------------------------------------------------------------------------


async def _update_pdf_key(
    scorecard_id: str,
    key: str,
    db_session_factory: Any,
) -> None:
    """Open a fresh DB session and update scorecards.report_pdf_key.

    Non-raising: errors are logged and swallowed so the background task
    does not crash the event loop.
    """
    from sqlalchemy import text as sa_text  # local import  # noqa: PLC0415

    try:
        async with db_session_factory() as session:
            await session.execute(
                sa_text(
                    "UPDATE scorecards SET report_pdf_key = :key "
                    "WHERE scorecard_id = :scorecard_id"
                ),
                {"key": key, "scorecard_id": scorecard_id},
            )
            await session.commit()
        log.info(
            "pdf_render.db_key_updated",
            scorecard_id=scorecard_id,
            # do NOT log the full key — it contains the scorecard_id only, no PII
        )
    except Exception as exc:  # broad catch — non-raising by design
        log.error(
            "pdf_render.db_key_update_failed",
            scorecard_id=scorecard_id,
            error_type=type(exc).__name__,
            error=str(exc),
        )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def render_scorecard_pdf(
    scorecard_id: str,
    session_id: str,
    candidate_name: str,
    job_title: str,
    language: str,
    scores: dict[str, int],
    composite_score: float,
    strengths: list[str],
    improvements: list[dict[str, str]],
    summary: str,
    *,
    settings: Settings,
    db_session_factory: Any = None,
) -> str | None:
    """Generate a PDF scorecard, upload to S3, return the S3 key or None on failure.

    Args:
        scorecard_id: UUID of the scorecards row (used as S3 key component).
        session_id: UUID of the interview session (used only for logging).
        candidate_name: Candidate's full name for the PDF header.
        job_title: Job title for the PDF header.
        language: BCP-47 language code ('en' | 'hi' | 'te').
        scores: dict mapping axis name to int score (0-10).
        composite_score: Weighted composite score (0.0-10.0).
        strengths: List of up to 3 strength strings.
        improvements: List of up to 3 dicts with 'area' and 'suggestion' keys.
        summary: 2-3 sentence summary paragraph.
        settings: Injected Settings instance (reads S3 credentials from env).
        db_session_factory: async_sessionmaker instance for updating the DB row.
                            If None, the DB update step is skipped.

    Returns:
        S3 key string on success (e.g. 'scorecards/{scorecard_id}/report.pdf'),
        or None on any failure. Never raises.
    """
    s3_key = f"{_SCORECARD_PREFIX}/{scorecard_id}/report.pdf"

    try:
        # _build_pdf_bytes is CPU-bound (ReportLab calls doc.build() which is
        # purely synchronous and can hold the event loop for 200-400 ms on a
        # typical scorecard).  Run it in the default thread-pool executor so
        # the event loop remains responsive to other concurrent requests.
        _build_fn = partial(
            _build_pdf_bytes,
            scorecard_id=scorecard_id,
            candidate_name=candidate_name,
            job_title=job_title,
            language=language,
            scores=scores,
            composite_score=composite_score,
            strengths=strengths,
            improvements=improvements,
            summary=summary,
        )
        pdf_bytes = await asyncio.to_thread(_build_fn)
    except Exception as exc:  # broad catch — non-raising by design
        log.error(
            "pdf_render.build_failed",
            scorecard_id=scorecard_id,
            session_id=session_id,
            error_type=type(exc).__name__,
            error=str(exc),
        )
        return None

    try:
        await _upload_to_s3(
            pdf_bytes=pdf_bytes,
            s3_key=s3_key,
            settings=settings,
        )
    except Exception as exc:  # broad catch — non-raising by design
        log.error(
            "pdf_render.upload_failed",
            scorecard_id=scorecard_id,
            session_id=session_id,
            error_type=type(exc).__name__,
            error=str(exc),
        )
        return None

    log.info(
        "pdf_render.uploaded",
        scorecard_id=scorecard_id,
        session_id=session_id,
        bucket=settings.s3_scorecard_bucket,
    )

    # Update DB if a session factory was provided.
    if db_session_factory is not None:
        await _update_pdf_key(scorecard_id, s3_key, db_session_factory)

    return s3_key
