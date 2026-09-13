"""D5 — publishing picks up the candidates who applied before anything was live.

The runner's comment promised early applicants "join a workflow when one goes
live". Nothing did it. They were enrolled with no workflow, and shortlisting
them later reported "no workflow attached" and started nothing — silently, for
exactly the openings HR had advertised before building the process.
"""

from __future__ import annotations

import inspect
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest


def test_waiting_means_live_undecided_and_not_in_a_round() -> None:
    """The count shown before publishing and the attach done by it must mean the
    same people — otherwise the confirmation promises one number and the
    publish moves another."""
    from app.workflows import _ATTACH_WAITING_SQL, _WAITING_COUNT_SQL

    predicate = (
        "WHERE requisition_id = :r AND deleted_at IS NULL AND workflow_id IS NULL"
        " AND current_round_id IS NULL AND status NOT IN ('hired', 'rejected')"
    )
    for sql in (_WAITING_COUNT_SQL, _ATTACH_WAITING_SQL):
        assert predicate in " ".join(sql.split())


@pytest.mark.asyncio
async def test_attaching_returns_who_moved_and_their_status() -> None:
    from app.workflows import attach_waiting_candidates

    a, b = uuid.uuid4(), uuid.uuid4()
    db = AsyncMock()
    db.execute = AsyncMock(return_value=MagicMock(
        all=MagicMock(return_value=[(a, "shortlisted"), (b, "new")])))
    got = await attach_waiting_candidates(db, requisition_id=uuid.uuid4(),
                                          workflow_id=uuid.uuid4())
    assert got == [(a, "shortlisted"), (b, "new")]
    sql = " ".join(str(db.execute.call_args.args[0]).split())
    assert sql.startswith("UPDATE enrolments SET workflow_id = :w")


def test_publish_attaches_and_starts_only_the_already_shortlisted() -> None:
    """Their human gate already happened. Everyone else waits for it — a publish
    must not shortlist anybody."""
    from app.routers.hr_workflows import publish_workflow

    src = inspect.getsource(publish_workflow)
    attach = src.index("attach_waiting_candidates(")
    commit = src.index("await db.commit()")
    assert attach < commit, "attaching must share the publish transaction"
    assert 'if st == "shortlisted":' in src
    assert "on_shortlisted(" in src
    assert "record_transition" not in src, "publishing must not move anyone's status"


def test_the_publish_refusal_attaches_nobody() -> None:
    from app.routers.hr_workflows import publish_workflow

    src = inspect.getsource(publish_workflow)
    assert src.index("if not report.publishable:") < src.index("attach_waiting_candidates(")
