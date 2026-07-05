"""General API endpoints for interview_core — S1-006.

Provides JWT-protected routes that prove cross-service token acceptance.
JWTs are issued by data_gateway and validated here using the shared secret.
"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter
from pydantic import BaseModel

from app.dependencies import CurrentUserDep

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/api", tags=["api"])


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class MeResponse(BaseModel):
    """Minimal identity extracted from a validated JWT."""

    user_id: str
    roles: list[str]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/me",
    response_model=MeResponse,
    summary="Return identity extracted from the caller's JWT",
    description=(
        "Validates the Bearer token issued by data_gateway and returns the "
        "subject (user_id) and roles. Proves cross-service JWT acceptance."
    ),
)
async def me(current_user: CurrentUserDep) -> MeResponse:
    """Return the user_id and roles encoded in the caller's JWT."""
    payload: dict[str, Any] = current_user
    user_id: str = payload["sub"]
    roles: list[str] = payload.get("roles", [])
    log.info("api.me", user_id=user_id, roles=roles)
    return MeResponse(user_id=user_id, roles=roles)
