"""Re-score completed sessions that have no scorecard yet.

Recovers interviews whose end-of-session scoring failed (e.g. the Gemini key
was missing at the time). Calls the real score_session() so behaviour is
identical to the live /internal/score path.

Run from services/feedback_billing:
    python rescore_missing.py
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
        # Completed sessions with >=2 turns and no scorecard row yet.
        rows = (
            await db.execute(
                text(
                    """
                    SELECT s.id, s.job_id, s.language
                    FROM sessions s
                    WHERE s.status = 'completed'
                      AND s.deleted_at IS NULL
                      AND NOT EXISTS (
                          SELECT 1 FROM scorecards sc WHERE sc.session_id = s.id
                      )
                    ORDER BY s.started_at DESC
                    """
                )
            )
        ).all()

    if not rows:
        print("No completed sessions are missing a scorecard. Nothing to do.")
        return

    print(f"Found {len(rows)} completed session(s) without a scorecard.\n")

    for sid, job_id, language in rows:
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
                    {"s": sid},
                )
            ).all()

            turns = [
                {"role": sp, "text": tc or ""}
                for sp, tc in turn_rows
                if tc and tc.strip()
            ]
            candidate_turns = sum(1 for t in turns if t["role"] == "candidate")
            if candidate_turns < 2:
                print(f"  SKIP {sid} — only {candidate_turns} candidate answer(s).")
                continue

            job_title = job[0] if job else "the role"
            level = (job[1] if job and job[1] in ("entry", "mid", "senior") else "entry")
            jd_text = (job[2] if job else "") or ""

            try:
                scorecard_id, scores, composite = await score_session(
                    session_id=str(sid),
                    job_title=job_title,
                    experience_level=level,
                    language=language or "en",
                    jd_text=jd_text,
                    turns=turns,
                    db_session=db,
                    settings=settings,
                )
                print(
                    f"  OK   {sid} -> scorecard {scorecard_id} "
                    f"composite={composite} scores={scores}"
                )
            except ScoringError as exc:
                print(f"  FAIL {sid} -> ScoringError: {exc.message}")
            except Exception as exc:  # noqa: BLE001
                print(f"  FAIL {sid} -> {type(exc).__name__}: {exc}")


if __name__ == "__main__":
    asyncio.run(main())
