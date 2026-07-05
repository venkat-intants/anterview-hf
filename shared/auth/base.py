"""AuthProvider abstract base + shared Pydantic models.

All AuthProvider implementations must fulfil this interface.
Call sites import from here — never from concrete implementations.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from pydantic import BaseModel, EmailStr, Field

# ---------------------------------------------------------------------------
# Shared data models
# ---------------------------------------------------------------------------


class AuthTokens(BaseModel):
    """Returned on register / login / refresh."""

    access_token: str
    refresh_token: str
    expires_in: int  # seconds until access_token expires
    user_id: str
    roles: list[str]


class User(BaseModel):
    """Minimal user representation returned by get_user()."""

    user_id: str
    full_name: str
    email: str
    roles: list[str]


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    full_name: str = Field(min_length=1)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class LogoutRequest(BaseModel):
    refresh_token: str | None = None


# ---------------------------------------------------------------------------
# Abstract provider interface
# ---------------------------------------------------------------------------


class AuthProvider(ABC):
    """Pluggable auth interface — swap implementations via AUTH_PROVIDER env var."""

    @abstractmethod
    async def register(
        self,
        email: str,
        password: str,
        full_name: str,
    ) -> AuthTokens:
        """Create a new user account and return tokens.

        Raises:
            ValueError: if email already exists.
        """

    @abstractmethod
    async def authenticate(self, email: str, password: str) -> AuthTokens:
        """Verify credentials and return tokens.

        Raises:
            ValueError: if credentials are invalid.
        """

    @abstractmethod
    async def refresh(self, refresh_token: str) -> AuthTokens:
        """Rotate refresh token and issue new access token.

        Raises:
            ValueError: if refresh token is invalid or expired.
        """

    @abstractmethod
    async def logout(
        self,
        *,
        access_token: str | None = None,
        refresh_token: str | None = None,
        current_user_id: str | None = None,
    ) -> None:
        """Invalidate session.

        ``current_user_id`` — when provided, the implementation MUST verify
        that the refresh token belongs to this user before revoking it.  If
        the token is owned by a different user the revocation is silently
        skipped (logged as a warning) to prevent cross-user token invalidation.
        """

    async def logout_all(self, user_id: str) -> int:
        """Revoke ALL active sessions for *user_id* ("log out all devices").

        Concrete (non-abstract) so providers that don't manage their own session
        store (e.g. external SSO) inherit a safe no-op instead of failing to
        instantiate. Implementations that own the session store (LocalAuthProvider)
        override this to purge every refresh token and invalidate outstanding
        access tokens.

        Returns the number of sessions revoked (0 for the default no-op).
        """
        return 0

    @abstractmethod
    async def get_user(self, user_id: str) -> User:
        """Return user profile for a given user_id.

        Raises:
            ValueError: if user not found.
        """
