"""Per-round auto-advance: HR decides, round by round, who moves people on.

``workflows.auto_advance_rounds`` was one switch for a WHOLE workflow, so a
hiring process could not be automatic through screening and deliberate at the
final round — which is the shape most hiring actually has.
``workflow_rounds.auto_advance`` overrides it per round.

The middle state is the point of these tests. NULL means "this round has no
opinion, follow the workflow", which is a genuinely different answer from
"automatic": collapsing the two would freeze every existing round at whatever
the workflow said on migration day, so later changing the workflow default
would stop reaching the rounds that never expressed a view.
"""

from __future__ import annotations

import pytest

from app.workflow_runner import auto_advance_for


def _round(auto_advance: bool | None) -> dict[str, object]:
    return {"title": "Round 1", "auto_advance": auto_advance}


def _workflow(default: bool) -> dict[str, object]:
    return {"auto_advance_rounds": default}


@pytest.mark.parametrize("workflow_default", [True, False])
def test_null_inherits_the_workflow_whichever_way_it_is_set(workflow_default: bool) -> None:
    """The default state, and the one every pre-existing round is in."""
    assert auto_advance_for(_round(None), _workflow(workflow_default)) is workflow_default


@pytest.mark.parametrize("workflow_default", [True, False])
def test_true_advances_even_where_the_workflow_says_manual(workflow_default: bool) -> None:
    """A round can opt INTO automation inside an otherwise manual process — a
    screening round in a deliberate workflow."""
    assert auto_advance_for(_round(True), _workflow(workflow_default)) is True


@pytest.mark.parametrize("workflow_default", [True, False])
def test_false_holds_even_where_the_workflow_says_automatic(workflow_default: bool) -> None:
    """And the case that motivated this: a final round that waits for a person
    inside an otherwise automatic process."""
    assert auto_advance_for(_round(False), _workflow(workflow_default)) is False


def test_a_round_that_predates_the_column_still_resolves() -> None:
    """Defensive: a row loaded by a query that has not been taught the column
    must inherit rather than raise, because a KeyError here would stop a
    candidate mid-process."""
    assert auto_advance_for({"title": "old"}, _workflow(True)) is True
    assert auto_advance_for({"title": "old"}, _workflow(False)) is False


def test_the_runner_reads_auto_advance_at_both_decision_points() -> None:
    """Passing and failing take different branches, and BOTH have to honour the
    per-round setting — an override respected on one path only would advance
    someone the round was set to hold.
    """
    import inspect

    from app import workflow_runner

    source = inspect.getsource(workflow_runner.record_result)
    assert source.count("auto_advance_for(") == 2, (
        "both the pass and fail paths must resolve the per-round override"
    )
    # And the old workflow-only read must be gone from the decision, or one
    # path would silently ignore the round's own setting.
    assert 'workflow["auto_advance_rounds"]' not in source


def test_holding_is_not_rejecting() -> None:
    """The load-bearing product guarantee, asserted against the source rather
    than assumed: this setting decides WHO MOVES PEOPLE ON, never who is
    rejected. A held candidate waits for a person; ending a candidacy goes
    through the final-decision path that demands a reason and a reason code.
    """
    import inspect

    from app import workflow_runner

    source = inspect.getsource(workflow_runner)
    for word in ("status = 'rejected'", '"rejected"'):
        assert word not in source, (
            f"workflow_runner must not be able to reject anyone (found {word!r})"
        )
