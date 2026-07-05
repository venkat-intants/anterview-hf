"""Naipunyam API client — S5-003a.

Implements the OAuth2 client_credentials token exchange and the four REST
profile APIs defined in LLD §9.  A module-level ``CircuitBreaker`` wraps every
outbound HTTP call so a flapping Naipunyam IdP degrades gracefully.

PII note: profile data (name, skills, …) is NEVER written to structured logs.
Only user IDs and status codes are logged.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import httpx
import structlog

from app.naipunyam.circuit_breaker import CircuitBreaker, CircuitOpenError

__all__ = [
    "NaipunyamClient",
    "NaipunyamError",
    "CircuitOpenError",
    "Profile",
    "Job",
    "Training",
    "Assessment",
    "naipunyam_cb",
]

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Module-level circuit breaker — shared by all NaipunyamClient instances.
# LLD §9 specifies: failure_threshold=5, recovery_timeout=30.0, half_open_max_calls=3
# ---------------------------------------------------------------------------
naipunyam_cb: CircuitBreaker = CircuitBreaker(
    name="naipunyam",
    failure_threshold=5,
    recovery_timeout=30.0,
    half_open_max_calls=3,
)


# ---------------------------------------------------------------------------
# Custom exception
# ---------------------------------------------------------------------------


class NaipunyamError(Exception):
    """Raised when the Naipunyam API returns a non-2xx response."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(f"Naipunyam API error {status_code}: {detail}")
        self.status_code = status_code
        self.detail = detail


# ---------------------------------------------------------------------------
# Minimal data-classes for API response shapes.
# Fields are representative; the exact API contract will be confirmed when
# APSSDC provides credentials.  Kept as dataclasses (not Pydantic) to avoid
# strict validation failures if the real API adds/renames fields.
# ---------------------------------------------------------------------------


@dataclass
class Profile:
    """Candidate profile returned by GET /v1/users/{uid}/profile."""

    uid: str
    name: str
    email: str = ""
    phone: str = ""
    preferred_language: str = "en"
    skills: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class Job:
    """An interested-job entry returned by GET /v1/users/{uid}/interested-jobs."""

    job_id: str
    title: str
    sector: str = ""
    nos_codes: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class Training:
    """A training history entry returned by GET /v1/users/{uid}/trainings."""

    training_id: str
    title: str
    completed: bool = False
    completed_at: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class Assessment:
    """An assessment result returned by GET /v1/users/{uid}/assessments."""

    assessment_id: str
    title: str
    score: float = 0.0
    passed: bool = False
    taken_at: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# NaipunyamClient
# ---------------------------------------------------------------------------


class NaipunyamClient:
    """Async client for the Naipunyam APSSDC REST API.

    Parameters
    ----------
    base_url:
        Base URL of the Naipunyam IdP, e.g. ``https://naipunyam.ap.gov.in``.
    client_id:
        OAuth2 client_id issued by APSSDC.
    client_secret:
        OAuth2 client_secret issued by APSSDC.

    All three parameters must be non-empty strings; a ``ValueError`` is raised
    at construction time if any is missing.  This surfaces misconfiguration
    early (at startup) rather than at first request.
    """

    def __init__(
        self,
        base_url: str,
        client_id: str,
        client_secret: str,
    ) -> None:
        if not base_url.strip():
            raise ValueError("NaipunyamClient: base_url must not be empty")
        if not client_id.strip():
            raise ValueError("NaipunyamClient: client_id must not be empty")
        if not client_secret.strip():
            raise ValueError("NaipunyamClient: client_secret must not be empty")

        self._base_url = base_url.rstrip("/")
        self._client_id = client_id
        self._client_secret = client_secret

        # Cached token state
        self._access_token: str = ""
        self._token_expires_at: float = 0.0  # monotonic time

        # Shared httpx async client — caller must close via aclose() or use
        # as an async context manager.
        self._http = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=httpx.Timeout(10.0),
        )

    # ------------------------------------------------------------------
    # Token management
    # ------------------------------------------------------------------

    async def _ensure_token(self) -> str:
        """Return a valid bearer token, refreshing 60 s before expiry.

        Uses client_credentials grant.  The token is cached in-process;
        no Redis caching is needed since client_credentials tokens are
        short-lived and non-user-specific.
        """
        pre_expiry_buffer = 60.0  # seconds

        now = time.monotonic()
        if self._access_token and now < self._token_expires_at - pre_expiry_buffer:
            return self._access_token

        log.info("naipunyam.token.refresh", client_id=self._client_id)

        response = await self._http.post(
            "/oauth/token",
            data={
                "grant_type": "client_credentials",
                "client_id": self._client_id,
                "client_secret": self._client_secret,
            },
        )

        if response.status_code >= 400:
            log.error(
                "naipunyam.token.error",
                status_code=response.status_code,
            )
            raise NaipunyamError(response.status_code, "Failed to obtain access token")

        payload: dict[str, Any] = response.json()
        self._access_token = payload["access_token"]
        expires_in: int = int(payload.get("expires_in", 3600))
        self._token_expires_at = now + expires_in

        log.info("naipunyam.token.obtained", expires_in=expires_in)
        return self._access_token

    # ------------------------------------------------------------------
    # Internal HTTP helper
    # ------------------------------------------------------------------

    async def _get(self, path: str) -> dict[str, Any]:
        """Perform an authenticated GET through the circuit breaker.

        Raises
        ------
        NaipunyamError
            On non-2xx responses.
        CircuitOpenError
            When the circuit breaker is OPEN.
        """
        token = await self._ensure_token()

        async def _do_get() -> httpx.Response:
            return await self._http.get(
                path,
                headers={"Authorization": f"Bearer {token}"},
            )

        response: httpx.Response = await naipunyam_cb.call(_do_get)

        if response.status_code >= 400:
            log.warning(
                "naipunyam.api.error",
                path=path,
                status_code=response.status_code,
            )
            raise NaipunyamError(response.status_code, response.text)

        result: dict[str, Any] = response.json()
        return result

    # ------------------------------------------------------------------
    # Public profile APIs
    # ------------------------------------------------------------------

    async def get_profile(self, uid: str) -> Profile:
        """Fetch the candidate's profile from Naipunyam.

        GET /v1/users/{uid}/profile

        PII: the returned ``Profile`` object must never be written to logs.
        """
        data = await self._get(f"/v1/users/{uid}/profile")
        log.info("naipunyam.profile.fetched", uid=uid)
        return Profile(
            uid=data.get("uid", uid),
            name=data.get("name", ""),
            email=data.get("email", ""),
            phone=data.get("phone", ""),
            preferred_language=data.get("preferred_language", "en"),
            skills=data.get("skills", []),
            raw=data,
        )

    async def get_interested_jobs(self, uid: str) -> list[Job]:
        """Fetch interested jobs for the candidate.

        GET /v1/users/{uid}/interested-jobs
        """
        data = await self._get(f"/v1/users/{uid}/interested-jobs")
        log.info("naipunyam.jobs.fetched", uid=uid)
        items: list[dict[str, Any]] = data if isinstance(data, list) else data.get("jobs", [])
        return [
            Job(
                job_id=j.get("job_id", ""),
                title=j.get("title", ""),
                sector=j.get("sector", ""),
                nos_codes=j.get("nos_codes", []),
                raw=j,
            )
            for j in items
        ]

    async def get_training_history(self, uid: str) -> list[Training]:
        """Fetch training history for the candidate.

        GET /v1/users/{uid}/trainings
        """
        data = await self._get(f"/v1/users/{uid}/trainings")
        log.info("naipunyam.trainings.fetched", uid=uid)
        items: list[dict[str, Any]] = data if isinstance(data, list) else data.get("trainings", [])
        return [
            Training(
                training_id=t.get("training_id", ""),
                title=t.get("title", ""),
                completed=t.get("completed", False),
                completed_at=t.get("completed_at", ""),
                raw=t,
            )
            for t in items
        ]

    async def get_assessments(self, uid: str) -> list[Assessment]:
        """Fetch assessment results for the candidate.

        GET /v1/users/{uid}/assessments
        """
        data = await self._get(f"/v1/users/{uid}/assessments")
        log.info("naipunyam.assessments.fetched", uid=uid)
        items: list[dict[str, Any]] = (
            data if isinstance(data, list) else data.get("assessments", [])
        )
        return [
            Assessment(
                assessment_id=a.get("assessment_id", ""),
                title=a.get("title", ""),
                score=float(a.get("score", 0.0)),
                passed=a.get("passed", False),
                taken_at=a.get("taken_at", ""),
                raw=a,
            )
            for a in items
        ]

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def aclose(self) -> None:
        """Close the underlying httpx client."""
        await self._http.aclose()
