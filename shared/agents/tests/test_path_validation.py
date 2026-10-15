"""CommitSpec.path / Citation.href must not be re-aimable by the URL parser.

A leading-``//`` check alone misses ``/\\evil.example`` and ``/<TAB>/evil…``
(the WHATWG parser reads ``\\`` as ``/`` and strips TAB/LF/CR), and does not stop
``/hr/x/../../auth/logout`` from climbing out of its prefix.
"""

from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from shared.agents.schema import Citation, CommitSpec

HOSTILE = [
    "https://evil.example/x",
    "//evil.example/x",
    "/\\evil.example/x",
    "/\t/evil.example/x",
    "/\n/evil.example/x",
    "/\r/evil.example/x",
    "/hr/x\x00",
    "/hr/x\x7f",
    "/hr/x/../../auth/logout",
    "/hr/..",
    "/hr/./x",
    "/hr/%2e%2e/auth/logout",
    "/hr/%2E%2E/auth/logout",
    "/hr/%252e%252e/auth/logout",
    "/hr/x\\..\\auth",
    "no-slash",
]

_ID = uuid.uuid4()
REAL_COMMIT_PATHS = [
    "/hr/interviews",
    f"/hr/applicants/{_ID}",
    f"/hr/requisitions/{_ID}/workflows/apply",
    f"/hr/workflows/{_ID}/rounds",
    f"/hr/workflows/{_ID}/rounds/{uuid.uuid4()}/criteria",
    f"/hr/workflows/{_ID}",
]


@pytest.mark.parametrize("bad", HOSTILE)
def test_commit_path_rejects_hostile(bad: str) -> None:
    with pytest.raises(ValidationError):
        CommitSpec(method="POST", path=bad, label="x")


@pytest.mark.parametrize("bad", [*HOSTILE, "/hr/x?next=//evil", "/hr/x#frag"])
def test_commit_path_rejects_query_and_fragment(bad: str) -> None:
    with pytest.raises(ValidationError):
        CommitSpec(method="PATCH", path=bad, label="x")


@pytest.mark.parametrize("good", REAL_COMMIT_PATHS)
def test_commit_path_accepts_every_real_shape(good: str) -> None:
    assert CommitSpec(method="PUT", path=good, label="ok").path == good


@pytest.mark.parametrize(
    "outside",
    [
        "/auth/logout",
        "/admin/hr-managers",
        "/platform/companies",
        "/users/me",
        "/hr",  # the bare segment is not under "/hr/"
        "/hrx/applicants",
        "/HR/applicants",
        "/",
    ],
)
def test_commit_path_must_be_under_hr_prefix(outside: str) -> None:
    """Mirrors web/src/api/proposalGuard.ts ALLOWED_PREFIXES (['/hr/'])."""
    with pytest.raises(ValidationError):
        CommitSpec(method="POST", path=outside, label="x")


def test_commit_method_cannot_be_delete() -> None:
    """Every real constructor uses POST/PATCH/PUT; DELETE is not expressible."""
    with pytest.raises(ValidationError):
        CommitSpec(method="DELETE", path="/hr/applicants/x", label="x")  # type: ignore[arg-type]
    for m in ("POST", "PATCH", "PUT"):
        assert CommitSpec(method=m, path="/hr/x", label="x").method == m  # type: ignore[arg-type]


@pytest.mark.parametrize("bad", [h for h in HOSTILE if h != "no-slash"] + ["no-slash"])
def test_citation_href_rejects_hostile(bad: str) -> None:
    with pytest.raises(ValidationError):
        Citation(kind="applicant", id="a", label="x", href=bad)


@pytest.mark.parametrize(
    "good",
    [
        "/hr/applicants/abc",
        f"/hr/applicants/{_ID}?enrolment={uuid.uuid4()}",
        "/hr/enrolments/e1/evidence?decision=d1",
        "/superadmin",
        "/hr/documents/x#section-2",
    ],
)
def test_citation_href_still_allows_query_and_fragment(good: str) -> None:
    assert Citation(kind="applicant", id="a", label="x", href=good).href == good


def test_citation_href_none_is_legal() -> None:
    assert Citation(kind="applicant", id="a", label="x", href=None).href is None
