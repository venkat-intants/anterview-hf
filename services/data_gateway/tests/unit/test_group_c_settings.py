"""C9 — the workflow settings that decide what automation spends.

Two of them were stored, editable through the API and copied when a workflow
was cloned, and read by nothing: ``auto_score_on_apply`` and
``shortlist_ats_threshold``. Switching either one changed nothing and told the
person it had. These tests are about the reading, which is the part that was
missing; the rules themselves live in shared/agents/tests.
"""

from __future__ import annotations

import inspect
import uuid
from unittest.mock import AsyncMock

import pytest


# ===========================================================================
# auto_score_on_apply
# ===========================================================================
@pytest.mark.asyncio
@pytest.mark.parametrize(("row", "expected"), [(None, True), (1, False)])
async def test_scoring_is_on_unless_a_published_workflow_says_otherwise(
    row: int | None, expected: bool
) -> None:
    from app.workflows import scoring_on_apply_enabled

    db = AsyncMock()
    db.scalar = AsyncMock(return_value=row)
    assert await scoring_on_apply_enabled(db, uuid.uuid4()) is expected
    sql = " ".join(str(db.scalar.call_args.args[0]).split())
    # Only the PUBLISHED workflow decides: a draft nobody has published yet
    # must not switch off scoring for the opening that is running.
    assert "status = 'published'" in sql
    assert "NOT auto_score_on_apply" in sql


def test_the_reconciler_skips_openings_that_turned_scoring_off() -> None:
    from app.reconciliation import _UNSCORED_WORK_SQL

    sql = " ".join(_UNSCORED_WORK_SQL.split())
    assert "NOT EXISTS (SELECT 1 FROM workflows w WHERE w.id = e.workflow_id" in sql
    assert "NOT w.auto_score_on_apply)" in sql


def test_the_upload_path_checks_the_setting_before_paying_for_a_score() -> None:
    """The reconciler skipping it is not enough — the synchronous upload would
    otherwise still spend the call the setting exists to prevent."""
    from app.routers.hr_applicants import create_applicant

    src = inspect.getsource(create_applicant)
    guard = src.index("scoring_on_apply_enabled")
    assert guard < src.index("score_resume_remote("), "scored before checking the setting"


# ===========================================================================
# shortlist_ats_threshold
# ===========================================================================
def test_the_bar_is_read_on_the_same_scale_it_is_stored() -> None:
    """The column is 0-10 (a check constraint says so); enrolment ATS scores are
    0-100. Comparing them directly would make a bar of 7 match everyone."""
    from app.agents.watch_runner import OPENING_HEALTH_SQL

    sql = " ".join(str(OPENING_HEALTH_SQL).split())
    assert "e.ats_overall >= wf.thr * 10" in sql


def test_only_candidates_nobody_has_acted_on_are_offered() -> None:
    """Someone already shortlisted, or already in a round, is not waiting for
    this decision — offering them again would be noise that never clears."""
    from app.agents.watch_runner import OPENING_HEALTH_SQL

    sql = " ".join(str(OPENING_HEALTH_SQL).split())
    assert "e.status = 'new' AND e.current_round_id IS NULL" in sql


def test_the_bar_comes_from_the_published_workflow_only() -> None:
    from app.agents.watch_runner import OPENING_HEALTH_SQL

    sql = " ".join(str(OPENING_HEALTH_SQL).split())
    assert "SELECT w.shortlist_ats_threshold AS thr" in sql
    assert "w.status = 'published'" in sql


def test_no_score_can_start_a_workflow() -> None:
    """D-05, structurally. The runner marks a candidate shortlisted as it
    starts them (inside _move_to_round), but it is only ever reached through
    on_shortlisted — a person pressing the button. If the runner ever reads the
    ATS bar itself, a score has begun advancing candidates."""
    import app.workflow_runner as wr

    assert "shortlist_ats_threshold" not in inspect.getsource(wr)
    assert "A human confirmed the shortlist" in inspect.getsource(wr.on_shortlisted)
    # The bar is only ever counted, in the watcher's gathering query.
    from app.agents.watch_runner import OPENING_HEALTH_SQL

    assert "UPDATE" not in str(OPENING_HEALTH_SQL).upper().replace("UPDATED_AT", "")
