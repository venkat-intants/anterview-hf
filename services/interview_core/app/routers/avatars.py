"""GET /api/avatars — avatar catalog endpoint.

Returns the list of avatars available for the candidate to pick in the
interview UI. Only client-safe fields are returned (id, name, gender,
thumbnail_url). Server-side fields (replica_id, voice) are never exposed.

Auth: Bearer JWT required (same CurrentUserDep pattern as /api/sessions and
/api/rooms). A candidate must be logged in to fetch the picker list.
"""

from __future__ import annotations

from typing import Literal

import structlog
from fastapi import APIRouter, status
from pydantic import BaseModel, Field

from app.avatars import AVATARS
from app.dependencies import CurrentUserDep

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/api", tags=["avatars"])


# ---------------------------------------------------------------------------
# Pydantic response models — public-facing fields only
# ---------------------------------------------------------------------------


class AvatarItem(BaseModel):
    """A single avatar entry in the catalog response.

    replica_id and voice are intentionally omitted — those are server-side
    implementation details that the client never needs to see.
    """

    id: str = Field(..., description="Stable slug used as avatar_id in POST /api/sessions.")
    name: str = Field(..., description="Human-readable display name.")
    gender: Literal["male", "female"] = Field(..., description="Avatar gender.")
    thumbnail_url: str = Field(..., description="CDN URL for the picker preview clip/image.")


class AvatarListResponse(BaseModel):
    """Response body for GET /api/avatars."""

    avatars: list[AvatarItem] = Field(..., description="Full avatar catalog, order is stable.")


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

_CATALOG_RESPONSE = AvatarListResponse(
    avatars=[
        AvatarItem(
            id=av.id,
            name=av.name,
            gender=av.gender,
            thumbnail_url=av.thumbnail_url,
        )
        for av in AVATARS
    ]
)


@router.get(
    "/avatars",
    response_model=AvatarListResponse,
    status_code=status.HTTP_200_OK,
    summary="List available interview avatars",
    description=(
        "Returns the full catalog of avatars the candidate can select before "
        "starting an interview session. Pass the chosen ``id`` as ``avatar_id`` "
        "in POST /api/sessions. Only client-safe fields are returned — "
        "``replica_id`` and ``voice`` are server-side only."
    ),
)
async def list_avatars(
    current_user: CurrentUserDep,
) -> AvatarListResponse:
    """Return the static avatar catalog.

    The catalog is static (no DB call). The response is pre-built at import
    time and returned as-is — effectively a typed constant endpoint.
    """
    log.info("avatars.list", user_id=current_user.get("sub"))
    return _CATALOG_RESPONSE
