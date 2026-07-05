"""Backfill per-axis rationale onto scorecards scored before the rationale feature.

For each scorecard whose ``rationale`` is NULL/empty (and whose session has a
persisted transcript), re-runs the real scorer — which now emits rationale —
and replaces the old scorecard row. Idempotent: rows that already have rationale
are skipped.

Run from services/feedback_billing:
    python backfill_rationale.py
"""
from __future__ import annotations

import asyncio

from sqlalchemy import text

from app.config import settings
from app.database import get_session_factory, init_engine
from app.scorer import ScoringError, score_session


async def main() -> None:
    init_engine()
    factory = get_session_factory()

    async with factory() as db:
        rows = (
            await db.execute(
                text(
                    """
                    SELECT sc.scorecard_id, sc.session_id, s.job_id, s.language
                    FROM scorecards sc
                    JOIN sessions s ON s.id = sc.session_id
                    WHERE (sc.rationale IS NULL OR sc.rationale::text IN ('null', '{}'))
                    ORDER BY sc.created_at DESC
                    """
                )
            )
        ).all()

    if not rows:
        print("All scorecards already have rationale. Nothing to backfill.")
        return

    print(f"Found {len(rows)} scorecard(s) without rationale.\n")

    for scorecard_id, session_id, job_id, language in rows:
        async with factory() as db:
            job = (
                await db.execute(
                    text("SELECT title, level, description FROM jobs WHERE id = :j"),
                    {"j": job_id},
                )
            ).first()
            turn_rows = (
                await db.execute(
                    text(
                        "SELECT speaker, text_content FROM turns "
                        "WHERE session_id = :s ORDER BY turn_number"
                    ),
                    {"s": session_id},
                )
            ).all()

            turns = [
                {"role": sp, "text": tc or ""}
                for sp, tc in turn_rows
                if tc and tc.strip()
            ]
            candidate_turns = sum(1 for t in turns if t["role"] == "candidate")
            if candidate_turns < 2:
                print(
                    f"  SKIP {session_id} — only {candidate_turns} candidate "
                    f"answer(s) in the DB (no transcript to re-score)."
                )
                continue

            # Remove the old (rationale-less) scorecard so the UNIQUE(session_id)
            # constraint allows score_session to write a fresh row.
            await db.execute(
                text("DELETE FROM scorecards WHERE scorecard_id = :sc"),
                {"sc": scorecard_id},
            )
            await db.commit()

            job_title = job[0] if job else "the role"
            level = job[1] if job and job[1] in ("entry", "mid", "senior") else "entry"
            jd_text = (job[2] if job else "") or ""

            try:
                new_id, scores, composite = await score_session(
                    session_id=str(session_id),
                    job_title=job_title,
                    experience_level=level,
                    language=language or "en",
                    jd_text=jd_text,
                    turns=turns,
                    db_session=db,
                    settings=settings,
                )
                # Show that rationale was actually written.
                r = (
                    await db.execute(
                        text("SELECT rationale FROM scorecards WHERE scorecard_id = :sc"),
                        {"sc": new_id},
                    )
                ).scalar_one()
                axes = list(r.keys()) if isinstance(r, dict) else []
                print(
                    f"  OK   {session_id} -> scorecard {new_id} composite={composite} "
                    f"rationale_axes={axes}"
                )
            except ScoringError as exc:
                print(f"  FAIL {session_id} -> ScoringError: {exc.message}")
            except Exception as exc:  # noqa: BLE001
                print(f"  FAIL {session_id} -> {type(exc).__name__}: {exc}")


if __name__ == "__main__":
    asyncio.run(main())
