"""Shared auth package — AuthProvider interface + implementations."""

from shared.auth.base import AuthProvider, AuthTokens, LoginRequest, RegisterRequest, User
from shared.auth.factory import get_auth_provider

__all__ = [
    "AuthProvider",
    "AuthTokens",
    "LoginRequest",
    "RegisterRequest",
    "User",
    "get_auth_provider",
]
