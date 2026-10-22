"""The "round not ready" notification opens a page that exists (audit AD-F18).

It linked to ``/hr/workflows``, which has no route in the web app, so HR
clicking it landed on the 404 page. It now opens the opening's own workflow
builder (``/hr/requisitions/{id}/workflow``).
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest


def test_the_link_is_the_openings_workflow_builder() -> None:
    from app.job_tasks import _workflow_link

    rid = uuid.uuid4()
    assert _workflow_link({"requisition_id": rid}) == f"/hr/requisitions/{rid}/workflow"
    # A workflow row without its opening still lands somewhere real.
    assert _workflow_link({}) == "/hr/requisitions"


@pytest.mark.asyncio
async def test_issue_without_a_task_config_notifies_with_the_real_link(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app import job_tasks

    sent: list[dict[str, Any]] = []

    async def _notify(_db: object, **kw: Any) -> bool:
        sent.append(kw)
        return True

    monkeypatch.setattr(job_tasks, "create_notification", _notify)
    db = AsyncMock()
    res = MagicMock()
    res.mappings.return_value.first.return_value = None  # no round_tasks row
    db.execute = AsyncMock(return_value=res)
    rid = uuid.uuid4()

    out = await job_tasks.issue(
        db,
        enrolment={"company_id": uuid.uuid4(), "full_name": "Asha"},
        round_={"id": uuid.uuid4(), "title": "Take-home"},
        workflow={"created_by_user_id": uuid.uuid4(), "requisition_id": rid},
    )
    assert out is None
    assert sent[0]["kind"] == "round_not_ready"
    assert sent[0]["link"] == f"/hr/requisitions/{rid}/workflow"
