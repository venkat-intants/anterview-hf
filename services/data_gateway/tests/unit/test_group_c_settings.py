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


def test_a_score_starts_a_workflow_only_through_the_opt_in_path() -> None:
    """D-05, structurally — REWRITTEN, NOT REMOVED (migration a2c4e6f8b0d3).

    This test used to be ``test_no_score_can_start_a_workflow`` and asserted
    that the runner never read the ATS bar at all: "if the runner ever reads
    the ATS bar itself, a score has begun advancing candidates." That was the
    right guard for the invariant of its day, and it failed — correctly — the
    moment auto-shortlist was built, because auto-shortlist is precisely a
    score advancing candidates.

    The product owner chose that reversal. What it did NOT authorise is a score
    advancing candidates through any OTHER door, or without the workflow having
    opted in. So the guard narrows rather than disappears, and still fails the
    way the original did if a second path appears:

      1. the runner reads the bar in exactly ONE function, ``_auto_shortlist``;
      2. that function checks the opt-in BEFORE it reads the bar, so a
         workflow that did not switch it on is never touched by a score;
      3. round one is still started only by ``on_shortlisted`` — the automatic
         path calls it rather than reaching ``_move_to_round`` itself.

    Renamed because the old name would now be a false claim, which is a defect
    under this repo's documentation rule, not a cosmetic one.
    """
    import ast

    import app.workflow_runner as wr

    # A READ is the bare string used as a key — `workflow.get("shortlist_ats_
    # threshold")` or `row["shortlist_ats_threshold"]`. Matched on a Constant
    # whose value IS the name, which excludes prose for free: a docstring that
    # merely mentions it is one long Constant that only contains it. (The first
    # draft of this test matched any mention and flagged clears_shortlist_bar
    # for its docstring — a pure predicate that takes the bar as an argument
    # and cannot advance anyone.)
    tree = ast.parse(inspect.getsource(wr))
    readers = sorted(
        fn.name
        for fn in ast.walk(tree)
        if isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef)
        and any(
            isinstance(n, ast.Constant) and n.value == "shortlist_ats_threshold"
            for n in ast.walk(fn)
        )
    )
    assert readers == ["_auto_shortlist"], (
        f"the ATS bar is read by {readers} — a score must advance candidates "
        "through the single opt-in path and nowhere else"
    )

    body = inspect.getsource(wr._auto_shortlist)
    assert body.index('workflow.get("auto_shortlist")') < body.index(
        'workflow.get("shortlist_ats_threshold")'
    ), "the bar is read before the opt-in is checked"

    assert "on_shortlisted(" in body and "_move_to_round(" not in body, (
        "the automatic path must start round one through on_shortlisted, not "
        "around it"
    )

    # Unchanged from the original: the watcher only ever COUNTS the bar.
    from app.agents.watch_runner import OPENING_HEALTH_SQL

    assert "UPDATE" not in str(OPENING_HEALTH_SQL).upper().replace("UPDATED_AT", "")
