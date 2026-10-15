"""The company super admin's hiring view is READ-ONLY, tenant-scoped and minimal.

Owner's ruling 2026-10-12 (docs/ACCEPTED-RISKS.md AR-10): a company ``super_admin``
may SEE named candidates on the hiring board, read-only; only ``hr_manager``
decides. Candidate NAMES stay. What these tests pin is everything around them:

* the two routes accept GET only, behind ``super_admin`` only;
* the company is the SESSION's, never a parameter -- the handler cannot be given
  one, and another company's opening is a 404;
* the candidate-bearing sections are an ALLOWLIST projection, so a field added to
  HR's payload tomorrow (an email, a resume link, a score) does not reach the
  super admin until someone adds it on purpose;
* nothing in the payload is a credential, a link to a file, or a contact detail.

Deliberately KEPT, because the read-only screen renders them (RequisitionDashboard
.tsx HeldPool / Activity): the held-reason text and the stage-transition reason
text. Both may be HR-typed free text about a named candidate. They are asserted
here as present, so removing them is also a deliberate edit, and AR-10 lists them.
"""

from __future__ import annotations

import copy
import inspect
import re
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from shared.testing.route_authz import iter_api_routes, route_guards

COMPANY = uuid.uuid4()
ADMIN_ID = uuid.uuid4()


def _hr_payload() -> dict[str, Any]:
    """What ``build_requisition_dashboard`` returns -- including every field HR
    gets that a viewer must not, so the projection has something to remove."""
    rid = str(uuid.uuid4())
    return {
        "progress": {"applications": 12, "held": 2, "hired": 1, "awaiting_decision": 3},
        "definitions": {"progress.applications": "applications@1"},
        "registry_hash": "abc",
        "workflow_state": {"published_version": 2, "draft_version": None},
        "stage_timing": [{"key": "shortlisted", "label": "Shortlisted", "median_days": 2.0}],
        "scores": {"avg_ats": 71.0, "scored_applications": 9, "avg_composite": 64.0},
        "held_pool": [
            {
                "enrolment_id": str(uuid.uuid4()),
                "applicant_id": str(uuid.uuid4()),  # a handle into HR-only routes
                "full_name": "Asha Verma",
                "held_reason": "Scored 41% on the SQL round; HR note: call her back",
                "round_title": "SQL round",
                "ats_overall": 70,  # per-named-candidate score, not rendered
                "held_days": 4.0,
                # Fields that do not exist today but must never pass if they did:
                "email": "asha@example.com",
                "phone": "+91 99999 99999",
                "resume_url": "https://bucket.example/resumes/asha.pdf?X-Amz-Signature=abc",
                "interview_link": "https://app.example/interview?token=SECRET",
            }
        ],
        "attention": [
            {"key": "held", "severity": "info", "title": "2 held", "body": "…",
             "link": f"/hr/requisitions/{rid}/decisions"},
            {"key": "x", "severity": "info", "title": "t", "body": "b", "link": None},
        ],
        "manual_steps": [
            {"key": "held", "count": 2, "label": "Held", "link": f"/hr/requisitions/{rid}/decisions"}
        ],
        "activity": [
            {
                "occurred_at": "2026-10-11T10:00:00+00:00",
                "automated": False,
                "actor": "Ravi (HR)",
                "candidate": "Asha Verma",
                "enrolment_id": str(uuid.uuid4()),
                "from_status": "shortlisted",
                "to_status": "held",
                "from_round": None,
                "to_round": "SQL round",
                "reason": "Borderline; revisit after the panel",
                "email": "asha@example.com",
                "applicant_id": str(uuid.uuid4()),
            }
        ],
        "activity_summary": {"automated_7d": 3, "manual_7d": 1, "last_automated_at": None},
        "requisition": {"id": rid, "title": "Backend Engineer", "status": "open"},
        "rounds": [{"round_id": str(uuid.uuid4()), "title": "SQL round", "attempted": 5}],
        "has_published_workflow": True,
        "median_days_in_stage": 2.5,
        "still_being_read": 0,
    }


# ---------------------------------------------------------------------------
# The routes: GET only, super_admin only, no way to name a company
# ---------------------------------------------------------------------------
def _board_routes() -> list[tuple[str, str, Any]]:
    from fastapi import FastAPI

    from app.routers.company_board import router

    app = FastAPI()
    app.include_router(router)
    return list(iter_api_routes(app))


def test_the_board_router_has_exactly_two_routes_and_both_are_get() -> None:
    routes = _board_routes()
    assert sorted((m, p) for m, p, _r in routes) == [
        ("GET", "/admin/hiring-board"),
        ("GET", "/admin/requisitions/{requisition_id}/dashboard"),
    ]


def test_both_routes_require_super_admin_alone() -> None:
    for _m, path, route in _board_routes():
        g = route_guards(route)
        assert g.require and all(r == frozenset({"super_admin"}) for r in g.require), path
        assert "get_company_admin_ctx" in g.dep_names, path


def test_the_company_is_not_a_parameter_of_either_handler() -> None:
    """The tenancy comes from the session context; there is nothing to forge."""
    from app.routers import company_board

    for fn in (company_board.get_hiring_board, company_board.get_requisition_dashboard_read_only):
        params = set(inspect.signature(fn).parameters)
        assert not (params & {"company_id", "tenant_id", "company", "tenant"}), fn.__name__
        assert "ctx" in params
    for _m, path, route in _board_routes():
        assert route.dependant.query_params == [], path  # type: ignore[attr-defined]
        assert route.dependant.body_params == [], path  # type: ignore[attr-defined]
        assert [p.name for p in route.dependant.path_params] in ([], ["requisition_id"]), path  # type: ignore[attr-defined]


def test_the_handlers_perform_no_write() -> None:
    """Structural: neither handler (nor its module) can commit, add or execute."""
    from app.routers import company_board

    src = inspect.getsource(company_board)
    for forbidden in ("db.commit", "db.add", "db.execute", "db.flush", "db.delete",
                      "enqueue_email", "mint_token", "presign", "generate_presigned_url"):
        assert forbidden not in src, forbidden


@pytest.mark.asyncio
async def test_the_dashboard_is_built_for_the_sessions_company_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.routers import company_board

    build = AsyncMock(return_value=_hr_payload())
    monkeypatch.setattr(company_board, "build_requisition_dashboard", build)
    rid = uuid.uuid4()

    await company_board.get_requisition_dashboard_read_only(rid, (ADMIN_ID, COMPANY), MagicMock())

    build.assert_awaited_once()
    assert build.await_args.kwargs["company_id"] == COMPANY
    assert build.await_args.kwargs["requisition_id"] == rid


@pytest.mark.asyncio
async def test_the_board_is_built_for_the_sessions_company_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.routers import company_board

    board = AsyncMock(return_value={"openings": []})
    monkeypatch.setattr(company_board, "hiring_board", board)
    await company_board.get_hiring_board((ADMIN_ID, COMPANY), MagicMock())
    assert board.await_args.kwargs["company_id"] == COMPANY


@pytest.mark.asyncio
async def test_another_companys_opening_is_a_404_not_a_403() -> None:
    """The real ownership lookup, with a database that finds no row in THIS company."""
    from app.routers.company_board import get_requisition_dashboard_read_only

    db = MagicMock()
    result = MagicMock()
    result.mappings.return_value.first.return_value = None
    db.execute = AsyncMock(return_value=result)

    with pytest.raises(HTTPException) as exc:
        await get_requisition_dashboard_read_only(uuid.uuid4(), (ADMIN_ID, COMPANY), db)
    assert exc.value.status_code == 404
    # ... and the query was constrained to the session's company.
    params = db.execute.await_args.args[1]
    assert params["c"] == COMPANY


def test_the_session_context_reads_the_company_from_the_users_row() -> None:
    from app.routers.admin_hr import get_company_admin_ctx

    src = inspect.getsource(get_company_admin_ctx)
    assert "FROM users u" in src and "u.company_id" in src
    assert "companies c" in src and "deleted_at IS NULL" in src


# ---------------------------------------------------------------------------
# The payload: names, stage and counts -- nothing that enables an action
# ---------------------------------------------------------------------------
def test_names_stage_and_counts_survive() -> None:
    from app.company_board import read_only_dashboard

    out = read_only_dashboard(_hr_payload())
    (held,) = out["held_pool"]
    assert held["full_name"] == "Asha Verma"
    assert held["round_title"] == "SQL round" and held["held_days"] == 4.0
    (act,) = out["activity"]
    assert act["candidate"] == "Asha Verma"
    assert (act["from_status"], act["to_status"], act["to_round"]) == ("shortlisted", "held", "SQL round")
    assert out["progress"] == _hr_payload()["progress"]
    assert out["rounds"][0]["attempted"] == 5


def test_the_free_text_the_screen_renders_is_kept_and_listed() -> None:
    """Not removed because web/src/pages/hr/RequisitionDashboard.tsx renders both;
    named here so dropping them is deliberate and AR-10 stays true."""
    from app.company_board import read_only_dashboard

    out = read_only_dashboard(_hr_payload())
    assert out["held_pool"][0]["held_reason"].startswith("Scored 41%")
    assert out["activity"][0]["reason"].startswith("Borderline")


def test_handles_scores_and_contact_fields_are_dropped_from_candidate_rows() -> None:
    from app.company_board import (
        READ_ONLY_ACTIVITY_KEYS,
        READ_ONLY_HELD_KEYS,
        read_only_dashboard,
    )

    out = read_only_dashboard(_hr_payload())
    assert set(out["held_pool"][0]) == set(READ_ONLY_HELD_KEYS)
    assert set(out["activity"][0]) == set(READ_ONLY_ACTIVITY_KEYS)
    for dropped in ("applicant_id", "ats_overall", "email", "phone", "resume_url",
                    "interview_link"):
        assert dropped not in out["held_pool"][0]
    assert "applicant_id" not in out["activity"][0] and "email" not in out["activity"][0]


def test_deep_links_into_hr_screens_are_nulled_server_side() -> None:
    from app.company_board import read_only_dashboard

    out = read_only_dashboard(_hr_payload())
    assert all(a["link"] is None for a in out["attention"])
    assert all(s["link"] is None for s in out["manual_steps"])


def test_nothing_in_the_payload_looks_like_a_credential_contact_or_file_link() -> None:
    from app.company_board import read_only_dashboard

    bad_key = re.compile(r"email|phone|token|secret|password|resume|url|signed|presign|href|link$",
                         re.IGNORECASE)
    bad_value = re.compile(r"https?://|X-Amz-|[\w.+-]+@[\w-]+\.\w+|[?&]token=", re.IGNORECASE)
    leaks: list[str] = []

    def walk(node: Any, path: str) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                # `link: None` (nulled) and the free-text columns are the known exceptions
                if bad_key.search(str(k)) and v is not None:
                    leaks.append(f"{path}.{k}")
                walk(v, f"{path}.{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{path}[{i}]")
        elif isinstance(node, str) and bad_value.search(node):
            leaks.append(f"{path} = {node[:40]}")

    walk(read_only_dashboard(_hr_payload()), "$")
    assert not leaks, leaks


def test_the_projection_does_not_mutate_hrs_payload() -> None:
    from app.company_board import read_only_dashboard

    payload = _hr_payload()
    before = copy.deepcopy(payload)
    read_only_dashboard(payload)
    assert payload == before


def test_the_projection_tolerates_an_empty_dashboard() -> None:
    from app.company_board import read_only_dashboard

    out = read_only_dashboard({})
    assert out["held_pool"] == [] and out["activity"] == []
    assert out["attention"] == [] and out["manual_steps"] == []


def test_every_allowlisted_key_is_still_emitted_by_the_real_builder() -> None:
    """A rename in HR's builder must not silently turn a kept field into None."""
    from app import requisition_dashboard
    from app.company_board import READ_ONLY_ACTIVITY_KEYS, READ_ONLY_HELD_KEYS

    src = inspect.getsource(requisition_dashboard.gather_dashboard)
    for key in set(READ_ONLY_HELD_KEYS) | set(READ_ONLY_ACTIVITY_KEYS):
        assert f'"{key}"' in src, f"gather_dashboard no longer emits {key!r}"


@pytest.mark.asyncio
async def test_the_route_returns_the_projection_not_hrs_raw_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.routers import company_board

    monkeypatch.setattr(
        company_board, "build_requisition_dashboard", AsyncMock(return_value=_hr_payload())
    )
    out = await company_board.get_requisition_dashboard_read_only(
        uuid.uuid4(), (ADMIN_ID, COMPANY), MagicMock()
    )
    assert "applicant_id" not in out["held_pool"][0]
    assert "email" not in out["held_pool"][0]
    assert out["attention"][0]["link"] is None
