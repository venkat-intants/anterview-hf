"""Test hooks — run a background pass now instead of waiting for its timer.

For the browser end-to-end suite. A public application is scored by the
reconciler (every ten minutes, or sooner when woken), and an interview result
reaches its round through the reminder sweep (every five). A spec that waited on
either would take minutes and fail on timing; these run one pass on request.

Three locks, all required:
* the router is only mounted when TEST_HOOKS_ENABLED is true (otherwise every
  path here is a plain 404 — see main.py);
* Settings refuses to start with TEST_HOOKS_ENABLED on in production or staging;
* every call must carry X-Test-Hooks-Token equal to TEST_HOOKS_TOKEN (32+
  characters, compared in constant time).

The hooks only run the passes the service already runs on its own schedule.
They take no input and change nothing a pass would not change anyway.
"""

from __future__ import annotations

import dataclasses
import hmac
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, status

from app import reconciliation, reminders
from app.config import settings
from app.database import get_session_factory

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/test-hooks", tags=["test-hooks"], include_in_schema=False)


def require_test_hooks_token(
    x_test_hooks_token: Annotated[str | None, Header(alias="X-Test-Hooks-Token")] = None,
) -> None:
    """Refuse unless the header matches the configured token exactly."""
    expected = settings.test_hooks_token
    supplied = x_test_hooks_token or ""
    if not expected or not hmac.compare_digest(supplied.encode(), expected.encode()):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")


TokenDep = Annotated[None, Depends(require_test_hooks_token)]


@router.post("/reconcile")
async def run_reconcile_pass(_: TokenDep) -> dict[str, Any]:
    """One reconciliation pass: ingest uploads, score, embed, settle, scorecards."""
    result = await reconciliation.run_once(get_session_factory())
    log.info("test_hooks.reconcile", **result.as_dict())
    return result.as_dict()


@router.post("/reminders")
async def run_reminder_sweep(_: TokenDep) -> dict[str, Any]:
    """One reminder sweep: reminders, expiry and no-show notices, interview results."""
    result = await reminders.run_once(get_session_factory())
    body = dataclasses.asdict(result)
    log.info("test_hooks.reminders", **{k: v for k, v in body.items() if k != "failed_stages"})
    return body
