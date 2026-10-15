"""avatar_url accepts only an inline png/jpeg/webp data URI.

A remote URL would be a tracking pixel that fires when staff open the profile.
The rule must match `safeAvatarSrc` in web/src/lib/safeUrl.ts.
"""

from __future__ import annotations

import base64

import pytest
from pydantic import ValidationError

from app.routers.auth import UserProfileUpdate

_PAYLOAD = base64.b64encode(b"\xff\xd8\xff\xe0 not really a jpeg").decode()


@pytest.mark.parametrize("mime", ["png", "jpeg", "webp"])
def test_accepts_inline_image_data_uri(mime: str) -> None:
    uri = f"data:image/{mime};base64,{_PAYLOAD}"
    assert UserProfileUpdate(avatar_url=uri).avatar_url == uri


def test_accepts_what_the_frontend_downscaler_produces() -> None:
    """imageFileToDataUrl -> canvas.toDataURL('image/jpeg', 0.85), 256px max."""
    uri = "data:image/jpeg;base64," + base64.b64encode(b"\x00" * 60_000).decode()
    assert len(uri) < 800_000  # the existing length cap in update_profile
    assert UserProfileUpdate(avatar_url=uri).avatar_url == uri


@pytest.mark.parametrize("empty", [None, ""])
def test_none_and_empty_still_clear_the_avatar(empty: str | None) -> None:
    assert UserProfileUpdate(avatar_url=empty).avatar_url == empty


@pytest.mark.parametrize(
    "bad",
    [
        "https://tracker.example/pixel.gif",
        "http://tracker.example/p.png",
        "//tracker.example/p.png",
        "javascript:alert(1)",
        "data:image/svg+xml;base64,PHN2Zz48L3N2Zz4=",
        "data:text/html;base64,PGgxPg==",
        "data:image/gif;base64,R0lGOD",
        "data:image/jpeg;base64,",
        "data:image/jpeg,not-base64",
        "data:image/jpeg;base64,abc\n",
        "data:image/jpeg;base64,ab c",
        "data:image/jpeg;base64,abc<script>",
        " data:image/png;base64,abc",
        "DATA:image/png;base64,abc",
    ],
)
def test_rejects_everything_else(bad: str) -> None:
    with pytest.raises(ValidationError):
        UserProfileUpdate(avatar_url=bad)
