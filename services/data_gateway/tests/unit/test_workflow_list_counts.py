"""The workflow list's counts, and the join that got them wrong.

A workflow list row carries two numbers: how many rounds the workflow has, and
how many candidates are running it. Both come from one query that LEFT JOINs
``workflow_rounds`` and ``enrolments`` to the same workflow — which multiplies
them. Four rounds and eighteen enrolled candidates produce 72 rows, and a plain
``count(r.id)`` reported "72 rounds" on a four-round workflow.

It read correctly for as long as nobody had applied, which is exactly when a
workflow gets built and looked at. The first enrolment broke it silently, and
the number is on the screen someone uses to pick which version to edit.

Asserted against the SQL text rather than a database because that is where the
defect lived: the shape of the query, not the values in it. The arithmetic is
also checked directly, so the reason the DISTINCT is needed stays legible to
whoever reads this next.
"""

from __future__ import annotations

import re


def _list_query() -> str:
    """The SELECT that builds the workflow list rows, comments stripped.

    The comment above that query explains the bug by quoting the broken
    expression, so a scan of the raw source finds ``count(r.id)`` in prose and
    fails on the very sentence describing the fix. Only code is inspected.
    """
    import inspect

    from app.routers.hr_workflows import list_workflows

    code = [
        line for line in inspect.getsource(list_workflows).splitlines()
        if not line.strip().startswith("#")
    ]
    return " ".join(" ".join(code).split())


def test_round_count_is_distinct() -> None:
    """The regression. Without DISTINCT this is rounds x enrolled candidates."""
    sql = _list_query()
    assert "count(DISTINCT r.id)" in sql, (
        "count(r.id) over a join with enrolments counts the fan-out, not rounds"
    )


def test_enrolment_count_is_distinct_too() -> None:
    """The half that was already right — pinned so it stays that way."""
    assert "count(DISTINCT e.id)" in _list_query()


def test_no_undistincted_count_survives_in_the_query() -> None:
    """Catches a third counted join added later with the same mistake.

    Deliberately broader than the two assertions above: the bug is a property
    of counting across two independent one-to-many joins, so any future
    ``count(x.id)`` here is suspect by construction.
    """
    sql = _list_query()
    plain = [c for c in re.findall(r"count\((?!DISTINCT)[^)]*\)", sql) if "*" not in c]
    assert not plain, f"un-DISTINCTed counts over a fanned-out join: {plain}"


def test_soft_deleted_rounds_stay_excluded() -> None:
    """Adding DISTINCT must not drop the filter that was already correct."""
    sql = _list_query()
    assert "FILTER (WHERE r.deleted_at IS NULL)" in sql


def test_the_fanout_arithmetic_that_produced_seventy_two() -> None:
    """Why the count was wrong, in numbers, so the comment is checkable.

    Observed live: a four-round workflow with eighteen enrolled candidates
    reported 72 rounds. 4 x 18 = 72 — the join, not a miscount.
    """
    rounds, enrolled = 4, 18
    joined_rows = rounds * enrolled
    assert joined_rows == 72
    # What each count returns over those rows.
    assert joined_rows != rounds  # count(r.id)
    assert len({f"round-{i}" for i in range(rounds) for _ in range(enrolled)}) == rounds
