"""Integration tests for jobs REST endpoints — S2-002 / self-serve.

Runs against the real local Postgres (port 5433) and Redis (port 6379).
The migration seeds 3 jobs with stable UUIDs:
  11111111-1111-1111-1111-111111111111 — Junior Java Developer (en)
  22222222-2222-2222-2222-222222222222 — Sales Associate (en)
  33333333-3333-3333-3333-333333333333 — Data Entry Operator (en)

Self-serve tests (POST /jobs):
  - 201 with id + title for an authenticated user
  - 401 without token
  - created job is absent from GET /jobs (browse list)
  - created job is reachable via GET /jobs/{id}
  - seeded public jobs still appear in GET /jobs after filter change
  - description defaults to title when omitted
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.main import app

# ---------------------------------------------------------------------------
# Stable seed UUIDs from migration 0418
# ---------------------------------------------------------------------------
_JOB_JAVA_UUID = "11111111-1111-1111-1111-111111111111"
_JOB_SALES_UUID = "22222222-2222-2222-2222-222222222222"
_JOB_DATA_ENTRY_UUID = "33333333-3333-3333-3333-333333333333"

_REGISTER_URL = "/auth/register"
_LOGIN_URL = "/auth/login"
_JOBS_URL = "/jobs"


def _unique_email() -> str:
    return f"jobs-test-{uuid.uuid4().hex[:8]}@example.com"


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def client() -> AsyncClient:  # type: ignore[misc]
    """Spin up the full ASGI app (lifespan fires: DB + Redis + AuthProvider)."""
    async with AsyncClient(  # noqa: SIM117
        transport=ASGITransport(app=app),
        base_url="http://test",
        timeout=30.0,
    ) as ac:
        async with app.router.lifespan_context(app):
            yield ac  # type: ignore[misc]


@pytest_asyncio.fixture
async def auth_token(client: AsyncClient) -> str:
    """Register a throw-away user and return a valid Bearer token."""
    email = _unique_email()
    resp = await client.post(
        _REGISTER_URL,
        json={"email": email, "password": "Secur3Pass!", "full_name": "Jobs Test User"},
    )
    assert resp.status_code == 201, resp.text
    return str(resp.json()["access_token"])


# ---------------------------------------------------------------------------
# GET /jobs — list
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_jobs_200_returns_3_seeded(
    client: AsyncClient, auth_token: str
) -> None:
    """All 3 seeded jobs are returned for an authenticated request."""
    resp = await client.get(
        _JOBS_URL,
        headers={"Authorization": f"Bearer {auth_token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 3
    assert len(body["items"]) == 3
    assert body["page"] == 1
    assert body["per_page"] == 20

    # Verify shape of a single item
    titles = {item["title"] for item in body["items"]}
    assert "Junior Java Developer" in titles
    assert "Sales Associate" in titles
    assert "Data Entry Operator" in titles

    # Ordered by title ASC
    returned_titles = [item["title"] for item in body["items"]]
    assert returned_titles == sorted(returned_titles)


@pytest.mark.asyncio
async def test_list_jobs_pagination_per_page_2(
    client: AsyncClient, auth_token: str
) -> None:
    """per_page=2 returns 2 items but total remains 3."""
    resp = await client.get(
        _JOBS_URL,
        params={"per_page": 2},
        headers={"Authorization": f"Bearer {auth_token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 3
    assert len(body["items"]) == 2
    assert body["per_page"] == 2
    assert body["page"] == 1


@pytest.mark.asyncio
async def test_list_jobs_language_hi_returns_0(
    client: AsyncClient, auth_token: str
) -> None:
    """No Hindi-language jobs are seeded; ?language=hi must return 0 items."""
    resp = await client.get(
        _JOBS_URL,
        params={"language": "hi"},
        headers={"Authorization": f"Bearer {auth_token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 0
    assert body["items"] == []


@pytest.mark.asyncio
async def test_list_jobs_401_no_auth(client: AsyncClient) -> None:
    """List endpoint must reject requests without a Bearer token."""
    resp = await client.get(_JOBS_URL)
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# GET /jobs/{id} — single job
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_job_200_valid_uuid(
    client: AsyncClient, auth_token: str
) -> None:
    """Fetching the Java Developer seed job returns full detail."""
    resp = await client.get(
        f"{_JOBS_URL}/{_JOB_JAVA_UUID}",
        headers={"Authorization": f"Bearer {auth_token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == _JOB_JAVA_UUID
    assert body["title"] == "Junior Java Developer"
    assert body["level"] == "entry"
    assert body["language"] == "en"
    assert body["is_active"] is True
    # Full detail fields
    assert "SSC/N0501" in body["nos_codes"]
    assert "SSC/N9001" in body["nos_codes"]
    assert "java" in body["competencies"]["required"]
    assert "created_at" in body
    assert "updated_at" in body


@pytest.mark.asyncio
async def test_get_job_404_nonexistent_uuid(
    client: AsyncClient, auth_token: str
) -> None:
    """Querying a UUID that doesn't exist returns 404."""
    nonexistent = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    resp = await client.get(
        f"{_JOBS_URL}/{nonexistent}",
        headers={"Authorization": f"Bearer {auth_token}"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_job_401_no_auth(client: AsyncClient) -> None:
    """Single-job endpoint must reject requests without a Bearer token."""
    resp = await client.get(f"{_JOBS_URL}/{_JOB_JAVA_UUID}")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# B-033 — interview context fields
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_job_response_includes_context_fields(
    client: AsyncClient, auth_token: str
) -> None:
    """GET /jobs list items must include company_name, department, interview_type keys.

    The seeded rows were inserted before migration 20260529_0004, so
    company_name and department will be NULL.  interview_type must default
    to 'screening' (applied by the DB server_default on the column).
    """
    resp = await client.get(
        _JOBS_URL,
        headers={"Authorization": f"Bearer {auth_token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] >= 1

    for item in body["items"]:
        # Keys must be present in every item (even when the values are null)
        assert "company_name" in item, f"company_name missing from job {item.get('id')}"
        assert "department" in item, f"department missing from job {item.get('id')}"
        assert "interview_type" in item, f"interview_type missing from job {item.get('id')}"
        # Seeded rows have no company_name / department set
        assert item["company_name"] is None
        assert item["department"] is None
        # Server default must be applied
        assert item["interview_type"] == "screening"


@pytest.mark.asyncio
async def test_job_detail_includes_context_fields(
    client: AsyncClient, auth_token: str
) -> None:
    """GET /jobs/{id} detail must also expose company_name, department, interview_type."""
    resp = await client.get(
        f"{_JOBS_URL}/{_JOB_JAVA_UUID}",
        headers={"Authorization": f"Bearer {auth_token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "company_name" in body
    assert "department" in body
    assert "interview_type" in body
    assert body["interview_type"] == "screening"


# ---------------------------------------------------------------------------
# POST /jobs — self-serve custom job creation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_custom_job_201_returns_id_and_title(
    client: AsyncClient, auth_token: str
) -> None:
    """POST /jobs returns HTTP 201 with a valid UUID id and the submitted title."""
    resp = await client.post(
        _JOBS_URL,
        json={
            "title": "Custom Practice Job",
            "company_name": "Acme Corp",
            "department": "Engineering",
            "jd_text": "We are looking for a skilled engineer.",
            "level": "mid",
            "interview_type": "technical",
        },
        headers={"Authorization": f"Bearer {auth_token}"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert "id" in body
    assert "title" in body
    assert body["title"] == "Custom Practice Job"
    # id must be a valid UUID
    uuid.UUID(body["id"])


@pytest.mark.asyncio
async def test_create_custom_job_401_no_auth(client: AsyncClient) -> None:
    """POST /jobs must reject unauthenticated requests with HTTP 401."""
    resp = await client.post(
        _JOBS_URL,
        json={"title": "No-auth Job"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_create_custom_job_422_missing_title(
    client: AsyncClient, auth_token: str
) -> None:
    """POST /jobs with an empty title must return HTTP 422 validation error."""
    resp = await client.post(
        _JOBS_URL,
        json={"title": ""},
        headers={"Authorization": f"Bearer {auth_token}"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_created_job_absent_from_list(
    client: AsyncClient, auth_token: str
) -> None:
    """A user-created practice job must NOT appear in GET /jobs (the public browse list)."""
    # Create a custom job
    create_resp = await client.post(
        _JOBS_URL,
        json={"title": "Secret Practice Role"},
        headers={"Authorization": f"Bearer {auth_token}"},
    )
    assert create_resp.status_code == 201, create_resp.text
    new_id = create_resp.json()["id"]

    # Browse list must not contain it
    list_resp = await client.get(
        _JOBS_URL,
        headers={"Authorization": f"Bearer {auth_token}"},
    )
    assert list_resp.status_code == 200, list_resp.text
    listed_ids = {item["id"] for item in list_resp.json()["items"]}
    assert new_id not in listed_ids, "User-created job leaked into public browse list"


@pytest.mark.asyncio
async def test_created_job_fetchable_by_id(
    client: AsyncClient, auth_token: str
) -> None:
    """A user-created practice job IS reachable via GET /jobs/{id} for any authenticated user."""
    # Create a custom job
    create_resp = await client.post(
        _JOBS_URL,
        json={
            "title": "Fetchable Practice Role",
            "level": "senior",
            "interview_type": "hr",
        },
        headers={"Authorization": f"Bearer {auth_token}"},
    )
    assert create_resp.status_code == 201, create_resp.text
    new_id = create_resp.json()["id"]

    # Fetch by UUID
    get_resp = await client.get(
        f"{_JOBS_URL}/{new_id}",
        headers={"Authorization": f"Bearer {auth_token}"},
    )
    assert get_resp.status_code == 200, get_resp.text
    body = get_resp.json()
    assert body["id"] == new_id
    assert body["title"] == "Fetchable Practice Role"
    assert body["level"] == "senior"
    assert body["interview_type"] == "hr"


@pytest.mark.asyncio
async def test_seeded_jobs_still_appear_in_list_after_filter(
    client: AsyncClient, auth_token: str
) -> None:
    """Seeded public jobs (created_by_user_id NULL) still appear in GET /jobs.

    Creating a user-owned job must not affect the visibility of public jobs.
    """
    # Create a custom job first (to confirm it doesn't pollute the list)
    await client.post(
        _JOBS_URL,
        json={"title": "Should Not Appear In Browse"},
        headers={"Authorization": f"Bearer {auth_token}"},
    )

    list_resp = await client.get(
        _JOBS_URL,
        headers={"Authorization": f"Bearer {auth_token}"},
    )
    assert list_resp.status_code == 200, list_resp.text
    body = list_resp.json()

    # All 3 seeded jobs must still be present
    assert body["total"] == 3
    titles = {item["title"] for item in body["items"]}
    assert "Junior Java Developer" in titles
    assert "Sales Associate" in titles
    assert "Data Entry Operator" in titles


@pytest.mark.asyncio
async def test_create_custom_job_description_defaults_to_title(
    client: AsyncClient, auth_token: str
) -> None:
    """When description is omitted the job is created with description equal to title.

    This verifies the NOT NULL default logic — the insert must succeed and the
    detail endpoint must return a non-empty description.
    """
    title = "No Description Provided Role"
    create_resp = await client.post(
        _JOBS_URL,
        json={"title": title},
        headers={"Authorization": f"Bearer {auth_token}"},
    )
    assert create_resp.status_code == 201, create_resp.text
    new_id = create_resp.json()["id"]

    get_resp = await client.get(
        f"{_JOBS_URL}/{new_id}",
        headers={"Authorization": f"Bearer {auth_token}"},
    )
    assert get_resp.status_code == 200, get_resp.text
    body = get_resp.json()
    assert body["description"] == title
