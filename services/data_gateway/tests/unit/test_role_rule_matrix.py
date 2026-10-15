"""The role-rule matrix for data_gateway -- enforced, closed by default.

THE RULE (owner's ruling, 2026-10-12): *strict, rule-based access. A super admin
can SEE things (read-only), but only the authorised role takes decisions.*

This module makes that structural. It declares, as DATA, which roles may reach
which route (``RULES``), then reads the mounted application's real dependency
trees (``shared.testing.route_authz``) and asserts that the two agree exactly.
Four properties follow, and each has its own test:

1. CLOSED BY DEFAULT. Every mounted route must match a row. A new route with no
   row fails ``test_every_route_has_a_matrix_row``; nothing passes by omission.
   Rows for ``/hr/**`` mutations are exact; only reads and pure authoring
   families (exams, question banks, offer templates...) are covered by a family
   row, and a family row still pins the exact role set, so a new route that is
   looser than its family fails too.
2. THE MATRIX IS WHAT THE CODE DOES. ``observed == declared`` for every route.
   Loosen a guard (add ``super_admin`` to ``get_hr_company``, say) and every
   decision route fails at once.
3. THE RULE ITSELF. Independently of how rows were tagged, any mutating route
   that acts on a named candidate (path-matched, not row-matched) must be
   unreachable by ``super_admin``, ``admin``, ``platform_owner``, ``candidate``
   and ``guest_candidate``; and ``super_admin`` may mutate only approvals, its
   own company's users, decision-reason configuration and the document library.
4. RUNTIME, NOT JUST INTROSPECTION. For every route that is not open to a role,
   a real signed token holding only that role is sent through the real ASGI app
   and must be refused with the role gate's own 403 -- before any database
   access (the test runs with no database at all).

Where the code is not what the rule would say, the row says so in ``NOTE``
rather than quietly agreeing with the code: see ``APPROVAL_PEER`` and
``CORPUS``.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterator
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from shared.auth.jwt import issue_access_token
from shared.testing.route_authz import (
    ALL_ROLES,
    MUTATING_METHODS,
    Rule,
    effective_roles,
    find_rule,
    iter_api_routes,
    route_guards,
)

# ---------------------------------------------------------------------------
# Role sets
# ---------------------------------------------------------------------------
PUBLIC = frozenset({"*"})  # no account authenticates the route (public / link token)
HR = frozenset({"hr_manager"})
SA = frozenset({"super_admin"})
PO = frozenset({"platform_owner"})
INTERVIEW = frozenset({"interviewer", "hr_manager"})
CORPUS_ROLES = frozenset({"hr_manager", "super_admin"})
STAFF_AGENT = frozenset({"platform_owner", "super_admin", "hr_manager", "admin"})
PROFILE_VIEWERS = frozenset({"hr_manager", "super_admin", "admin", "platform_owner"})
# Any signed-in ACCOUNT acting on its own data: not a guest link, not a service token.
SELF = ALL_ROLES - {"guest_candidate", "service"}
ANY_TOKEN = ALL_ROLES  # get_current_user alone

# Dependency name -> marker label (see shared.testing.route_authz.effective_roles).
AUTH_MARKERS = {"get_current_user": "authed", "get_exam_link_ctx": "link"}

# ---------------------------------------------------------------------------
# Kinds. The sets below are what the invariant tests quantify over.
# ---------------------------------------------------------------------------
DECISION = "DECISION"  # advance/hold/reject/hire, stage moves, offers, interviews, outcomes
CANDIDATE_ACT = "CANDIDATE_ACT"  # other mutations of a named candidate's record
SCORING = "SCORING"  # interviewer scorecards
APPROVAL = "APPROVAL"  # approves / sends back, super_admin only
APPROVAL_READ = "APPROVAL_READ"  # the approval queues
APPROVAL_PEER = "APPROVAL_PEER"  # question-bank peer review by HR (two-person rule)
SA_VIEW = "SA_VIEW"  # the read-only hiring board
USER_MGMT = "USER_MGMT"  # create / delete accounts
CONFIG = "CONFIG"  # company configuration that is not a candidate decision
CORPUS = "CORPUS"  # the HR document library
PLATFORM = "PLATFORM"  # platform_owner console
HR_READ = "HR_READ"  # GET under /hr, named-candidate reads included
AUTHORING = "AUTHORING"  # exams, banks, templates, workflow design: no candidate
AGENT = "AGENT"  # console copilots (narrowed inline, see tests below)
PROFILE_READ = "PROFILE_READ"
SELF_SERVICE = "SELF_SERVICE"
PUBLIC_KIND = "PUBLIC"

CANDIDATE_KINDS = frozenset({DECISION, CANDIDATE_ACT})
# SELF_SERVICE: an account editing its OWN resume/profile/notifications; AGENT: copilots
# that only ever return proposals. Neither can name another person's candidate record.
SA_MAY_MUTATE = frozenset({APPROVAL, USER_MGMT, CONFIG, CORPUS, SELF_SERVICE, AGENT})

_M = {"GET": "GET", "POST": "POST", "PUT": "PUT", "PATCH": "PATCH", "DELETE": "DELETE"}


def _rows(kind: str, roles: frozenset[str], *specs: str) -> list[Rule]:
    """``"POST /hr/x"`` -> exact row; ``"* /hr/x/**"`` -> any method."""
    out: list[Rule] = []
    for spec in specs:
        method, path = spec.split(" ", 1)
        out.append(Rule(None if method == "*" else frozenset({_M[method]}), path, roles, kind))
    return out


RULES: list[Rule] = [
    # ---- A. HIRING DECISIONS and anything that moves a candidate: hr_manager ONLY
    *_rows(
        DECISION, HR,
        "POST /hr/applicants/{applicant_id}/decision",
        "PATCH /hr/applicants/{applicant_id}",
        "POST /hr/enrolments/{enrolment_id}/status",
        "POST /hr/enrolments/{enrolment_id}/decision",
        "POST /hr/enrolments/{enrolment_id}/round-review",
        "POST /hr/enrolments/{enrolment_id}/release-hold",
        "POST /hr/enrolments/{enrolment_id}/reapply-override",
        "POST /hr/enrolments/{enrolment_id}/exceptions",
        "POST /hr/exceptions/{exception_id}/resolve",
        "PATCH /hr/exceptions/{exception_id}",
        "POST /hr/applicants/merge",
        "POST /hr/requisitions/{requisition_id}/split",
        "POST /hr/requisitions/{requisition_id}/merge",
        "POST /hr/interviews",
        "PATCH /hr/interviews/{invite_id}",
        "POST /hr/interviews/{invite_id}/revoke",
        "POST /hr/exams/{exam_id}/assignments",
        "POST /hr/exams/{exam_id}/assignments/{aid}/revoke",
        "POST /hr/enrolments/{enrolment_id}/loops",
        "POST /hr/loops/{loop_id}/sessions",
        "POST /hr/loops/{loop_id}/send",
        "POST /hr/loops/{loop_id}/cancel",
        "PATCH /hr/sessions/{session_id}",
        "POST /hr/sessions/{session_id}/outcome",
        "POST /hr/enrolments/{enrolment_id}/offers",
        "PATCH /hr/offers/{offer_id}",
        "POST /hr/offers/{offer_id}/submit",
        "POST /hr/offers/{offer_id}/recall",
        "POST /hr/offers/{offer_id}/reopen",
        "POST /hr/offers/{offer_id}/send",
        "POST /hr/offers/{offer_id}/resend",
        "POST /hr/offers/{offer_id}/withdraw",
        "POST /hr/offers/{offer_id}/preboarding-complete",
        "POST /hr/offers/{offer_id}/hrms-export",
        "POST /hr/documents/{document_id}/review",
        "POST /hr/pools/{pool_id}/members/{member_id}/invite",
        "POST /hr/enrolments/{enrolment_id}/scorecards",
        "POST /hr/scorecards/{scorecard_id}/withdraw",
    ),
    *_rows(
        CANDIDATE_ACT, HR,
        "POST /hr/applicants",
        "POST /hr/applicants/bulk",
        "POST /hr/applicants/reindex",
        "POST /hr/applicants/{applicant_id}/rescore",
        "POST /hr/applicants/{applicant_id}/accommodations",
        "POST /hr/accommodations/{accommodation_id}/revise",
        "POST /hr/accommodations/{accommodation_id}/revoke",
        "POST /hr/code-integrity-findings",
        "POST /hr/enrolments/{enrolment_id}/checkins",
        "POST /hr/checkins/{checkin_id}/correct",
        "POST /hr/task-submissions/{submission_id}/reissue",
        "POST /hr/task-submissions/{submission_id}/withdraw",
        "POST /hr/documents/{document_id}/download",
        "POST /hr/rediscovery/search",
        "POST /hr/pools",
        "PATCH /hr/pools/{pool_id}",
        "DELETE /hr/pools/{pool_id}",
        "POST /hr/pools/{pool_id}/members",
        "DELETE /hr/pools/{pool_id}/members/{member_id}",
        "POST /hr/pools/{pool_id}/members/{member_id}/evidence-reviewed",
        "POST /hr/interviewers/{user_id}/availability",
        "DELETE /hr/availability/{window_id}",
        "PUT /hr/interviewers/{user_id}/capacity",
    ),
    # ---- B. Interviewer scoring: the interviewer (an HR manager may sit on a panel)
    *_rows(SCORING, INTERVIEW, "* /interviewer/**"),
    # ---- C. Approvals / send-backs: super_admin ONLY (the submitter is HR; the
    #         author != approver rule is enforced in the service layer and pinned
    #         by tests/integration/test_ph4_*_guarantees.py, see AUTHOR_NE_APPROVER)
    *_rows(
        APPROVAL, SA,
        "POST /hr/requisitions/{requisition_id}/approval/approve",
        "POST /hr/requisitions/{requisition_id}/approval/reject",
        "POST /admin/offers/{offer_id}/approve",
        "POST /admin/offers/{offer_id}/reject",
        "POST /admin/workflow-reviews/{workflow_id}/approve",
        "POST /admin/workflow-reviews/{workflow_id}/request-changes",
        "POST /admin/bank-questions/{qid}/approve",
        "POST /admin/bank-questions/{qid}/request-changes",
    ),
    *_rows(
        APPROVAL_READ, SA,
        "GET /hr/requisitions/approvals/pending",
        "GET /admin/offer-approvals",
        "GET /admin/offers/{offer_id}",
        "GET /admin/workflow-reviews",
        "GET /admin/workflow-reviews/{workflow_id}",
        "GET /admin/question-reviews",
    ),
    # NOTE (not the "super_admin only" reading of the rule): question-bank review
    # also has an HR PEER path so a company with several HR managers is not
    # blocked on its super admin. The two-person rule is enforced in
    # app/question_banks.py (author, submitter and any editor are refused). This
    # is a deliberate design predating the ruling; it is NOT widened here, and is
    # reported to the owner for a decision.
    *_rows(
        APPROVAL_PEER, HR,
        "POST /hr/bank-questions/{qid}/approve",
        "POST /hr/bank-questions/{qid}/request-changes",
        "POST /hr/question-banks/{bank_id}/questions/approve-all",
    ),
    # ---- D. The super admin's read-only view of hiring
    *_rows(
        SA_VIEW, SA,
        "GET /admin/hiring-board",
        "GET /admin/requisitions/{requisition_id}/dashboard",
    ),
    # ---- E. Users and companies
    *_rows(USER_MGMT, SA, "* /admin/hr-managers/**", "* /admin/hr-managers",
           "* /admin/interviewers/**", "* /admin/interviewers"),
    *_rows(PLATFORM, PO, "* /admin/companies/**", "* /admin/companies",
           "GET /admin/platform-stats", "GET /admin/scheduled-jobs", "GET /admin/audit-log",
           "* /admin/feature-flags/**", "GET /admin/feature-flags",
           "GET /admin/email-events", "GET /admin/email-events/summary"),
    *_rows(CONFIG, SA, "* /admin/decision-reasons/**", "* /admin/decision-reasons"),
    # NOTE: super_admin can mutate the HR document library. It is company policy
    # text, not a named candidate, so it is on the SA_MAY_MUTATE list; the pools /
    # rediscovery routers (which DO name candidates) are hr_manager only above.
    *_rows(CORPUS, CORPUS_ROLES, "* /hr/library/**", "* /hr/library", "POST /hr/library/search"),
    *_rows(HR_READ, CORPUS_ROLES, "GET /hr/metrics/definitions"),
    *_rows(PROFILE_READ, PROFILE_VIEWERS, "GET /users/{user_id}/profile"),
    # ---- F. Copilots. Declared reach is the four staff roles; the handlers narrow
    #         panel -> hr_manager and watchers/run -> platform_owner inline (tested
    #         below by calling the handlers).
    *_rows(AGENT, STAFF_AGENT, "POST /agent/chat", "POST /agent/watchers/run",
           "POST /agent/panel/{applicant_id}"),
    *_rows(SELF_SERVICE, ANY_TOKEN, "GET /agent/status", "POST /auth/logout",
           "POST /consent", "GET /consent/status", "DELETE /consent"),
    # ---- G. An account acting on its OWN data (guests and service tokens refused)
    *_rows(SELF_SERVICE, SELF, "* /users/me/**", "* /notifications/**", "* /notifications",
           "* /jobs/**", "* /jobs",
           "POST /auth/logout-all", "GET /auth/me", "PATCH /auth/me/profile",
           "POST /auth/change-password", "POST /auth/resend-verification"),
    # ---- H. Interviewer-free HR authoring FAMILIES (no named candidate). GET reads of
    #         everything else under /hr are one row; a NEW mutation outside these
    #         families needs its own exact row above.
    Rule(frozenset(MUTATING_METHODS), "/hr/exams/**", HR, AUTHORING),
    Rule(frozenset(MUTATING_METHODS), "/hr/question-banks/**", HR, AUTHORING),
    Rule(frozenset(MUTATING_METHODS), "/hr/bank-questions/**", HR, AUTHORING),
    Rule(frozenset(MUTATING_METHODS), "/hr/offer-templates/**", HR, AUTHORING),
    Rule(frozenset(MUTATING_METHODS), "/hr/offer-templates", HR, AUTHORING),
    Rule(frozenset(MUTATING_METHODS), "/hr/document-requirements/**", HR, AUTHORING),
    Rule(frozenset(MUTATING_METHODS), "/hr/questions/**", HR, AUTHORING),
    Rule(frozenset(MUTATING_METHODS), "/hr/workflows/**", HR, AUTHORING),
    Rule(frozenset(MUTATING_METHODS), "/hr/rounds/**", HR, AUTHORING),
    # AI drafting of coding questions into a bank. Already inside the
    # /hr/question-banks/** family above; pinned EXACTLY as well because it is
    # the one bank route that spends LLM budget, so loosening the family row
    # must not quietly widen who can reach the model. Preview only: it writes
    # nothing and approves nothing.
    *_rows(AUTHORING, HR, "POST /hr/question-banks/{bank_id}/questions/generate-coding"),
    *_rows(
        AUTHORING, HR,
        "POST /hr/requisitions",
        "PATCH /hr/requisitions/{requisition_id}",
        "POST /hr/requisitions/{requisition_id}/status",
        "POST /hr/requisitions/{requisition_id}/confirm",
        "PUT /hr/requisitions/{requisition_id}/jd/draft",
        "DELETE /hr/requisitions/{requisition_id}/jd/draft",
        "POST /hr/requisitions/{requisition_id}/jd/versions/{version_id}/publish",
        "POST /hr/requisitions/{requisition_id}/approval/submit",
        "PUT /hr/requisitions/{requisition_id}/publish-schedule",
        "DELETE /hr/requisitions/{requisition_id}/publish-schedule",
        "POST /hr/requisitions/{requisition_id}/workflows",
        "POST /hr/requisitions/{requisition_id}/workflows/apply",
        "POST /hr/requisitions/{requisition_id}/workflows/from-template",
        "POST /hr/requisitions/{requisition_id}/questions",
        "PUT /hr/requisitions/{requisition_id}/questions/order",
        "POST /hr/requisitions/{requisition_id}/document-requirements",
    ),
    Rule(frozenset({"GET"}), "/hr/**", HR, HR_READ),
    # ---- I. Public / link-token routes: exact, because a new public route is the
    #         single most dangerous addition and must be named to exist.
    *_rows(
        PUBLIC_KIND, PUBLIC,
        "GET /", "GET /metrics", "GET /health/live", "GET /health/deep",
        "POST /auth/register", "POST /auth/login", "POST /auth/refresh",
        "POST /auth/verify-email", "POST /auth/forgot-password", "POST /auth/reset-password",
        "GET /auth/sso/naipunyam/initiate", "POST /auth/sso/naipunyam/callback",
        "GET /auth/sso/google/initiate", "GET /auth/sso/google/callback",
        "GET /careers/{slug}",
        "GET /exam", "POST /exam/camera-consent", "POST /exam/start", "POST /exam/submit",
        "POST /exam/submit-round", "POST /exam/submit-coding", "POST /exam/run-code",
        "POST /exam/run-code-custom", "POST /exam/integrity-event",
        "GET /interview-invite", "POST /interview-invite/redeem", "POST /interview-invite/resume",
        "GET /offer", "POST /offer/code", "POST /offer/accept", "POST /offer/decline",
        "POST /offer/documents/code", "POST /offer/documents/session",
        "POST /offer/documents/consent/withdraw", "GET /offer/documents",
        "POST /offer/documents/{requirement_id}",
        "GET /task", "POST /task/start", "POST /task/consent/withdraw",
        "PUT /task/responses/{item_key}", "POST /task/artifacts",
        "DELETE /task/artifacts/{response_id}", "POST /task/submit",
        "GET /task/materials/{material_id}/download",
        "GET /apply/activate/target", "POST /apply/activate",
        "POST /apply/{requisition_id}/draft", "GET /apply/draft", "PATCH /apply/draft",
        "POST /apply/draft/confirm", "DELETE /apply/draft", "POST /apply/draft/resume-upload",
        "POST /apply/draft/submit", "GET /apply/{requisition_id}", "POST /apply/{requisition_id}",
    ),
]

#: Kinds whose reach the invariants pin to a single role set.
EXPECTED_BY_KIND: dict[str, frozenset[str]] = {
    DECISION: HR,
    CANDIDATE_ACT: HR,
    SCORING: INTERVIEW,
    APPROVAL: SA,
    APPROVAL_READ: SA,
    APPROVAL_PEER: HR,
    SA_VIEW: SA,
}

#: Mutating routes under /hr that name a candidate by PATH, whatever their row
#: says. Used by the independent check (property 3).
_CANDIDATE_PATH = re.compile(
    r"^/hr/(applicants|enrolments|interviews|pools|loops|sessions|scorecards|exceptions|"
    r"task-submissions|checkins|documents|accommodations|offers|rediscovery)\b"
    r"|^/hr/exams/\{exam_id\}/assignments"
    r"|^/hr/requisitions/\{requisition_id\}/(split|merge)$"
)

#: Roles that must never reach a candidate-acting route (everything but hr_manager,
#: plus interviewer, whose only route is the scorecard family).
NOT_CANDIDATE_ACTORS = ALL_ROLES - HR


def _app() -> object:
    from app.main import app

    return app


def _observed() -> list[tuple[str, str, object, frozenset[str]]]:
    out = []
    for method, path, route in iter_api_routes(_app()):
        out.append((method, path, route, effective_roles(route_guards(route, AUTH_MARKERS))))
    return out


@pytest.fixture(scope="module")
def observed() -> list[tuple[str, str, object, frozenset[str]]]:
    rows = _observed()
    assert len(rows) > 300, "route discovery failed -- the matrix would pass vacuously"
    return rows


# ---------------------------------------------------------------------------
# 1. Closed by default, and the matrix is what the code does
# ---------------------------------------------------------------------------
def test_every_route_has_a_matrix_row(observed: list) -> None:
    missing = [f"{m} {p}" for m, p, _r, _e in observed if find_rule(RULES, m, p) is None]
    assert not missing, (
        "routes with NO row in the role-rule matrix -- classify each one (who may reach it, "
        f"and is it a decision/approval/read?) before it ships: {missing}"
    )


def test_every_route_is_reachable_by_exactly_the_declared_roles(observed: list) -> None:
    wrong = []
    for method, path, _route, eff in observed:
        rule = find_rule(RULES, method, path)
        if rule is not None and eff != rule.roles:
            wrong.append(
                f"{method} {path} [{rule.kind}]: code allows {sorted(eff)}, "
                f"matrix says {sorted(rule.roles)}"
            )
    assert not wrong, "guard drift:\n  " + "\n  ".join(wrong)


def test_no_matrix_row_is_stale(observed: list) -> None:
    """A row naming a route that no longer exists documents an authorisation that
    is not there. Exact rows must each match at least one live route."""
    live = {(m, p) for m, p, _r, _e in observed}
    stale = []
    for rule in RULES:
        if rule.path.endswith("/**"):
            if not any(rule.matches(m, p) for m, p in live):
                stale.append(f"{sorted(rule.methods or [])} {rule.path}")
            continue
        methods = rule.methods or frozenset(_M)
        if not any((m, rule.path) in live for m in methods):
            stale.append(f"{sorted(rule.methods or [])} {rule.path}")
    assert not stale, f"matrix rows with no live route: {stale}"


# ---------------------------------------------------------------------------
# 2. The rule itself
# ---------------------------------------------------------------------------
def test_each_sensitive_kind_has_its_single_authorised_role_set(observed: list) -> None:
    bad = []
    for method, path, _route, eff in observed:
        rule = find_rule(RULES, method, path)
        if rule is None:
            continue
        want = EXPECTED_BY_KIND.get(rule.kind)
        if want is not None and eff != want:
            bad.append(f"{method} {path} [{rule.kind}] reachable by {sorted(eff)}")
    assert not bad, bad


def test_hiring_decisions_are_hr_manager_only(observed: list) -> None:
    decisions = [
        (m, p, e) for m, p, _r, e in observed
        if (find_rule(RULES, m, p) or Rule(None, "", frozenset(), "")).kind in CANDIDATE_KINDS
    ]
    assert len(decisions) >= 50, "the decision set shrank -- did the matrix lose its rows?"
    for m, p, eff in decisions:
        assert eff == HR, f"{m} {p} is reachable by {sorted(eff)}; a decision is hr_manager-only"


def test_every_approval_is_super_admin_only(observed: list) -> None:
    approvals = [
        (m, p, e) for m, p, _r, e in observed
        if (find_rule(RULES, m, p) or Rule(None, "", frozenset(), "")).kind == APPROVAL
    ]
    assert len(approvals) == 8
    for m, p, eff in approvals:
        assert eff == SA, f"{m} {p} reachable by {sorted(eff)}"


def test_a_super_admin_alone_cannot_mutate_any_candidate_state(observed: list) -> None:
    """The headline invariant, computed from the CODE, not from the rows' tags.

    Every route a bare ``super_admin`` token passes the role gate on, which also
    mutates: it must be an approval, user management of its own company, decision
    reason configuration or the policy document library -- nothing that names a
    candidate.
    """
    offenders = []
    reachable_mutations = []
    for method, path, _route, eff in observed:
        if method not in MUTATING_METHODS or "super_admin" not in eff:
            continue
        reachable_mutations.append(f"{method} {path}")
        kind = (find_rule(RULES, method, path) or Rule(None, "", frozenset(), "?")).kind
        if kind not in SA_MAY_MUTATE:
            offenders.append(f"{method} {path} [{kind}]")
    assert reachable_mutations, "found no super_admin mutation at all -- discovery is broken"
    assert not offenders, f"super_admin can mutate outside its remit: {offenders}"


def test_any_route_that_names_a_candidate_by_path_refuses_every_non_hr_role(
    observed: list,
) -> None:
    """Independent of the row tags: classification by PATH, so mis-tagging a
    decision route as something softer cannot hide it."""
    checked = 0
    for method, path, _route, eff in observed:
        if method not in MUTATING_METHODS or not _CANDIDATE_PATH.search(path):
            continue
        checked += 1
        leaked = eff & NOT_CANDIDATE_ACTORS
        assert not leaked, f"{method} {path} is reachable by {sorted(leaked)}"
    assert checked >= 50


def test_admin_and_platform_owner_take_no_per_candidate_decision(observed: list) -> None:
    for method, path, _route, eff in observed:
        if method not in MUTATING_METHODS:
            continue
        kind = (find_rule(RULES, method, path) or Rule(None, "", frozenset(), "?")).kind
        if kind in CANDIDATE_KINDS | {SCORING, APPROVAL, APPROVAL_PEER}:
            assert not (eff & {"admin", "platform_owner"}), f"{method} {path}"
    # ... and the platform console is platform_owner alone: `admin` (analytics)
    # does not reach company or feature-flag administration.
    for method, path, _route, eff in observed:
        kind = (find_rule(RULES, method, path) or Rule(None, "", frozenset(), "?")).kind
        if kind == PLATFORM:
            assert eff == PO, f"{method} {path}"


def test_guest_and_candidate_reach_none_of_the_gated_surfaces(observed: list) -> None:
    gated = (
        CANDIDATE_KINDS
        | {SCORING, APPROVAL, APPROVAL_READ, APPROVAL_PEER, SA_VIEW, USER_MGMT, CONFIG, CORPUS,
           PLATFORM, HR_READ, AUTHORING, AGENT, PROFILE_READ}
    )
    checked = 0
    for method, path, _route, eff in observed:
        kind = (find_rule(RULES, method, path) or Rule(None, "", frozenset(), "?")).kind
        if kind in gated:
            checked += 1
            assert not (eff & {"guest_candidate", "candidate", "service", "*"}), f"{method} {path}"
    assert checked > 250


def test_the_super_admin_view_is_get_only_and_super_admin_only(observed: list) -> None:
    board = [
        (m, p, e) for m, p, _r, e in observed
        if (find_rule(RULES, m, p) or Rule(None, "", frozenset(), "")).kind == SA_VIEW
    ]
    assert {p for _m, p, _e in board} == {
        "/admin/hiring-board",
        "/admin/requisitions/{requisition_id}/dashboard",
    }
    for m, p, eff in board:
        assert m == "GET", f"{m} {p}: the read-only view must not accept a write"
        assert eff == SA


def test_no_route_can_be_mutated_through_a_path_that_looks_like_a_read(observed: list) -> None:
    """Nothing under the read-only prefixes may answer a write method."""
    for method, path, _route, _eff in observed:
        if path.startswith("/admin/hiring-board"):
            assert method == "GET"


# ---------------------------------------------------------------------------
# Inline narrowing the dependency tree cannot show (agent handlers)
# ---------------------------------------------------------------------------
def _user(*roles: str) -> MagicMock:
    u = MagicMock()
    u.user_id = "11111111-1111-1111-1111-111111111111"
    u.roles = list(roles)
    return u


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["super_admin", "admin", "hr_manager"])
async def test_the_watcher_sweep_is_platform_owner_only_inline(role: str) -> None:
    from app.routers.agent import run_watchers_now

    with pytest.raises(HTTPException) as exc:
        await run_watchers_now(_user(role))
    assert exc.value.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["super_admin", "admin", "platform_owner"])
async def test_the_candidate_panel_is_hr_manager_only_inline(
    role: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.routers import agent as agent_router

    ctx = MagicMock()
    ctx.role = role
    monkeypatch.setattr(agent_router, "_agent_context", AsyncMock(return_value=ctx))
    monkeypatch.setattr(agent_router.settings, "agents_enabled", True, raising=False)
    gather = AsyncMock()
    monkeypatch.setattr(agent_router, "gather_candidate_evidence", gather)

    with pytest.raises(HTTPException) as exc:
        await agent_router.agent_panel(uuid.uuid4(), _user(role), MagicMock(), MagicMock())
    assert exc.value.status_code == 403
    gather.assert_not_called()  # refused BEFORE any candidate evidence is read


# ---------------------------------------------------------------------------
# 4. Runtime: a real token, the real app, no database
# ---------------------------------------------------------------------------
def _token(role: str) -> str:
    from app.config import settings

    return str(
        issue_access_token(
            user_id=str(uuid.uuid4()),
            roles=[role],
            secret=settings.jwt_secret,
            algorithm=settings.jwt_algorithm,
            issuer=settings.jwt_issuer,
            audience=settings.jwt_audience,
        )
    )


def _fill(path: str) -> str:
    return re.sub(r"\{[^}]+\}", lambda _m: str(uuid.uuid4()), path)


@pytest_asyncio.fixture
async def client() -> Iterator[AsyncClient]:  # type: ignore[misc]
    async with AsyncClient(
        transport=ASGITransport(app=_app()), base_url="http://test"  # type: ignore[arg-type]
    ) as ac:
        yield ac  # type: ignore[misc]


#: Kinds proved at runtime. Everything whose reach is a single role set.
_RUNTIME_KINDS = (
    CANDIDATE_KINDS
    | {SCORING, APPROVAL, APPROVAL_READ, APPROVAL_PEER, SA_VIEW, USER_MGMT, CONFIG, PLATFORM}
)


@pytest.mark.asyncio
async def test_a_real_token_without_the_role_is_refused_by_the_role_gate(
    client: AsyncClient, observed: list
) -> None:
    """Every refused (route, role) pair, over real HTTP, with no database.

    The assertion is on the role gate's own message, not merely 403: the other
    403 on these routes ("not assigned to a company") needs a database, so seeing
    the role-gate text proves it is THIS control that refused.
    """
    tokens = {role: _token(role) for role in ALL_ROLES}
    pairs = 0
    failures = []
    for method, path, _route, eff in observed:
        rule = find_rule(RULES, method, path)
        if rule is None or rule.kind not in _RUNTIME_KINDS:
            continue
        for role in sorted(ALL_ROLES - eff):
            pairs += 1
            resp = await client.request(
                method, _fill(path), headers={"Authorization": f"Bearer {tokens[role]}"},
                json={} if method in MUTATING_METHODS else None,
            )
            if resp.status_code != 403 or "Insufficient permissions" not in resp.text:
                failures.append(f"{role} {method} {path} -> {resp.status_code} {resp.text[:80]}")
    assert pairs > 400, "too few (route, role) pairs exercised -- discovery is broken"
    assert not failures, "\n".join(failures[:20])


# ---------------------------------------------------------------------------
# The matrix's own behaviour: these prove "closed by default" is not vacuous
# ---------------------------------------------------------------------------
def test_a_new_mutating_route_under_hr_has_no_row_until_someone_classifies_it() -> None:
    assert find_rule(RULES, "POST", "/hr/applicants/{applicant_id}/hire") is None
    assert find_rule(RULES, "POST", "/hr/enrolments/{enrolment_id}/reject-all") is None
    assert find_rule(RULES, "POST", "/admin/offers/{offer_id}/override") is None
    assert find_rule(RULES, "POST", "/brand-new-prefix/thing") is None


def test_a_new_unguarded_route_is_seen_as_public_and_so_mismatches_its_family() -> None:
    from fastapi import APIRouter, FastAPI

    probe = FastAPI()
    r = APIRouter()

    @r.post("/hr/exams/{exam_id}/sneaky")  # inside an authoring family, with NO guard
    async def sneaky(exam_id: str) -> dict[str, str]:
        return {}

    probe.include_router(r)
    (method, path, route), = list(iter_api_routes(probe))
    rule = find_rule(RULES, method, path)
    assert rule is not None and rule.roles == HR
    assert effective_roles(route_guards(route, AUTH_MARKERS)) == PUBLIC != rule.roles
