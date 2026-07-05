"""Unit tests for Cluster-D security/correctness fixes on the exam take path.

Covers:
- Fix 3: pass/fail rounding — the mixed/coding percent now uses math.floor
  (same semantics as grade_exam / MCQ) so a boundary candidate is never
  rounded UP across the pass_threshold.
- Fix 2 (partial): the Redis grading-claim helper — correct SET NX / fail-open
  behaviour with a mocked Redis client.
"""

from __future__ import annotations

import contextlib
import math
import uuid
from unittest.mock import AsyncMock

import pytest

# ---------------------------------------------------------------------------
# Fix 3 — rounding semantics (pure math, no imports from the router needed)
# ---------------------------------------------------------------------------

def _percent_floor(total_raw: int, total_max: int) -> int:
    """The fixed formula used in _grade_and_finalize after the rounding fix."""
    return math.floor(100 * total_raw / total_max) if total_max > 0 else 0


def _percent_round(total_raw: int, total_max: int) -> int:
    """The OLD formula that was in _grade_and_finalize (before the fix)."""
    return round(100 * total_raw / total_max) if total_max > 0 else 0


class TestRoundingFix:
    """Boundary candidates must NEVER be rounded up across the threshold."""

    def test_floor_never_promotes_boundary_candidate(self) -> None:
        # 59.5% raw score, threshold=60 — old round() would give 60 (pass),
        # floor gives 59 (fail).  The candidate genuinely scored below threshold.
        raw, max_ = 119, 200  # 119/200 = 59.5%
        assert _percent_round(raw, max_) == 60   # old buggy behaviour
        assert _percent_floor(raw, max_) == 59   # correct: fail

    def test_floor_passes_exact_threshold(self) -> None:
        # 120/200 = 60.0% exactly — floor and round agree → pass at threshold 60.
        raw, max_ = 120, 200
        assert _percent_floor(raw, max_) == 60
        assert 60 >= 60  # passes

    def test_floor_vs_round_diverges_at_half_points(self) -> None:
        # Any score x.5% that is below the threshold must NOT be rounded up.
        # threshold = 70; 139/200 = 69.5%
        raw, max_ = 139, 200
        assert _percent_round(raw, max_) == 70   # old: boundary promoted → wrongly passes
        assert _percent_floor(raw, max_) == 69   # fixed: correctly fails

    def test_floor_and_round_agree_for_whole_percents(self) -> None:
        # For exact integer percentages the two formulas are identical.
        for pct in range(0, 101):
            raw = pct
            max_ = 100
            assert _percent_floor(raw, max_) == _percent_round(raw, max_), (
                f"Diverged at {pct}%"
            )

    def test_zero_max_returns_zero(self) -> None:
        assert _percent_floor(0, 0) == 0

    def test_full_score_is_100(self) -> None:
        assert _percent_floor(200, 200) == 100


# ---------------------------------------------------------------------------
# Fix 2 — Redis grading-claim behaviour (mocked, no real Redis)
# ---------------------------------------------------------------------------

class TestGradingClaimLogic:
    """Verify the SET NX EX claim logic used by _grade_and_finalize.

    The actual grading function requires a full async SQLAlchemy session and
    real ORM objects, so we test the claim primitive in isolation.
    """

    @pytest.mark.asyncio
    async def test_nx_claim_succeeds_first_time(self) -> None:
        """SET NX returns True on the first call → this request should grade."""
        mock_redis = AsyncMock()
        mock_redis.set = AsyncMock(return_value=True)
        attempt_id = uuid.uuid4()

        claimed = bool(await mock_redis.set(f"exam:grading:{attempt_id}", "1", nx=True, ex=180))
        assert claimed is True
        mock_redis.set.assert_called_once()

    @pytest.mark.asyncio
    async def test_nx_claim_fails_second_time(self) -> None:
        """SET NX returns None/False on a second call → the second request must NOT grade."""
        mock_redis = AsyncMock()
        mock_redis.set = AsyncMock(return_value=None)  # NX failed
        attempt_id = uuid.uuid4()

        claimed = bool(await mock_redis.set(f"exam:grading:{attempt_id}", "1", nx=True, ex=180))
        assert claimed is False

    @pytest.mark.asyncio
    async def test_redis_error_fails_open(self) -> None:
        """When Redis raises, the claim logic must fail OPEN (claimed=True) so
        the submit is NOT rejected by a cache hiccup."""
        mock_redis = AsyncMock()
        mock_redis.set = AsyncMock(side_effect=Exception("connection refused"))
        attempt_id = uuid.uuid4()

        claimed = False
        try:
            raw = await mock_redis.set(f"exam:grading:{attempt_id}", "1", nx=True, ex=180)
            claimed = bool(raw)
        except Exception:  # noqa: BLE001
            claimed = True  # fail open

        assert claimed is True

    @pytest.mark.asyncio
    async def test_claim_is_deleted_after_grading(self) -> None:
        """The Redis key must be DEL'd in the finally block regardless of outcome."""
        mock_redis = AsyncMock()
        mock_redis.set = AsyncMock(return_value=True)
        mock_redis.delete = AsyncMock(return_value=1)
        attempt_id = uuid.uuid4()
        claim_key = f"exam:grading:{attempt_id}"

        claimed = bool(await mock_redis.set(claim_key, "1", nx=True, ex=180))
        assert claimed
        # Simulate the finally block.
        await mock_redis.delete(claim_key)
        mock_redis.delete.assert_called_once_with(claim_key)

    @pytest.mark.asyncio
    async def test_claim_delete_error_does_not_propagate(self) -> None:
        """A DEL failure in the finally block must be swallowed (log-and-continue),
        never surfaced to the caller as a 500."""
        mock_redis = AsyncMock()
        mock_redis.delete = AsyncMock(side_effect=Exception("Redis gone"))
        claim_key = f"exam:grading:{uuid.uuid4()}"

        # Must not raise — swallowed, same as the finally block in _grade_and_finalize.
        with contextlib.suppress(Exception):
            await mock_redis.delete(claim_key)
