"""Workflow dry run — PH4-O2.

What a simulation is
--------------------
Synthetic candidates (SIM-001, SIM-002, …) walked through a workflow version
using :func:`app.workflows.route_after_result` — the function the runner calls
for real candidates. A dry run therefore tests the logic that will run, not a
second implementation of it that could quietly disagree — including the
runner's one rule outside that function: with "advance rounds automatically"
off it moves nobody, so a simulated candidate stops where a real one would.

The scenarios are chosen to cover every branch at least once: the path where
everyone passes, and for each round and each of its exits (pass, fast-track,
below threshold) one candidate who reaches that round and takes that exit. With
at most twelve rounds and three exits that is a few dozen scenarios, not the
exponential set of every combination — enough to prove each branch goes
somewhere a person will see.

Alongside the scenarios, every round is checked for what would stop it running
or deserves a second look: missing questions, an exam that is still a draft, an
AI interview with nothing to assess, a branch that points nowhere, a loop, an
unreachable round, a scorecard round with no criteria. Errors mean the version
cannot safely run; warnings mean it can, but someone should look.

What a simulation never does
----------------------------
It writes exactly two rows: its own result (``workflow_simulations``) and an
audit entry. It creates no applicant, enrolment, invite, exam link, offer or
notification; it enqueues no email and moves no stage. That is not a flag the
engine checks — the simulation simply never calls anything that writes those
things. ``tests/unit/test_ph4_wave2.py`` holds that structurally, and the smoke
counts every sensitive table before and after a run.

A passing simulation publishes nothing. It says the tested paths work.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog
from app.workflows import (
    AI_GRADED_KINDS,
    EXAM_BACKED_KINDS,
    HUMAN_EVALUATED_KINDS,
    MAX_ROUNDS,
    ROUND_KINDS,
    TASK_KINDS,
    branch_errors,
    build_coverage,
    exam_round_problem,
    exam_round_readiness,
    graph_problems,
    load_criteria,
    load_rounds,
    round_edges,
    route_after_result,
    workflow_fingerprint,
)

log = structlog.get_logger(__name__)

ERROR, WARNING = "error", "warning"
#: Scenario count is bounded by construction (one per branch), and by this.
MAX_SCENARIOS = 3 * MAX_ROUNDS + 1


class SimulationError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


@dataclass
class Finding:
    severity: str
    message: str
    round_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"severity": self.severity, "message": self.message, "round_id": self.round_id}


@dataclass
class _Checks:
    findings: list[Finding] = field(default_factory=list)

    def error(self, message: str, round_id: str | None = None) -> None:
        self.findings.append(Finding(ERROR, message, round_id))

    def warn(self, message: str, round_id: str | None = None) -> None:
        self.findings.append(Finding(WARNING, message, round_id))


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------
def _simulated_percent(round_: dict[str, Any], outcome: str) -> float | None:
    """A score that produces ``outcome`` on this round, or None for a person's verdict."""
    if round_["kind"] in HUMAN_EVALUATED_KINDS:
        return None
    threshold = round_.get("pass_threshold")
    if threshold is None:
        return None
    threshold = float(threshold)
    if outcome == "fast_track":
        return float(round_["fast_track_min_percent"])
    if outcome == "pass":
        return threshold
    return max(0.0, threshold - 10.0)


def _outcomes(round_: dict[str, Any]) -> list[str]:
    out = ["pass", "fail"]
    if round_.get("on_fast_track_next_round_id") and round_.get("fast_track_min_percent") is not None:
        out.insert(1, "fast_track")
    return out


def _walk(
    by_id: dict[str, dict[str, Any]],
    start: str,
    forced: dict[str, str],
    auto_advance: bool = True,
) -> tuple[list[dict[str, Any]], str]:
    """Walk one synthetic candidate from ``start``. Returns (steps, end state).

    ``forced`` fixes the outcome at particular rounds; everywhere else the
    candidate passes. End state is ``decision`` (reached the final human
    decision), ``held`` (stopped for a person), ``waiting`` (passed, and stays
    on the round until a person moves them — rounds do not advance
    automatically) or ``error`` (the path ran into a loop or a branch that
    points nowhere).
    """
    steps: list[dict[str, Any]] = []
    seen: set[str] = set()
    cur: str | None = start
    while cur is not None:
        if cur in seen or len(steps) > MAX_ROUNDS:
            steps.append({"round_id": cur, "round_title": by_id.get(cur, {}).get("title"),
                          "error": "the path returns to a round it already visited"})
            return steps, "error"
        seen.add(cur)
        round_ = by_id.get(cur)
        if round_ is None:
            steps.append({"round_id": cur, "round_title": None,
                          "error": "a branch points at a round that does not exist"})
            return steps, "error"
        outcome = forced.get(cur, "pass")
        percent = _simulated_percent(round_, outcome)
        # THE RUNNER'S OWN ROUTING. A human review is a person's verdict:
        # passed is what they said, and a fast-track does not apply.
        route = route_after_result(round_, passed=outcome != "fail", percent=percent)
        step: dict[str, Any] = {
            "round_id": cur,
            "round_title": round_.get("title"),
            "kind": round_["kind"],
            "outcome": outcome,
            "simulated_percent": percent,
            "branch": route.branch,
        }
        if not auto_advance:
            # workflow_runner: below the threshold the candidate is held (a fail
            # branch is not followed); a pass leaves them on the round for a
            # person to move. Either way the walk ends here.
            if outcome == "fail":
                step["next"] = {"kind": "hold"}
                steps.append(step)
                return steps, "held"
            step["next"] = {"kind": "person"}
            steps.append(step)
            return steps, "waiting"
        if route.kind == "advance":
            nxt = by_id.get(route.next_round_id or "")
            step["next"] = {"kind": "round", "round_id": route.next_round_id,
                            "round_title": nxt.get("title") if nxt else None}
            steps.append(step)
            cur = route.next_round_id
            continue
        step["next"] = {"kind": route.kind}  # complete → the final decision; hold
        steps.append(step)
        return steps, "decision" if route.kind == "complete" else "held"
    return steps, "decision"


def _shortest_prefix(by_id: dict[str, dict[str, Any]], first: str, target: str) -> dict[str, str] | None:
    """The outcomes that bring a candidate from ``first`` to ``target`` fastest.

    Breadth-first over every branch, so a round only reachable through a
    fast-track or a fail branch is still visited.
    """
    queue: list[tuple[str, dict[str, str]]] = [(first, {})]
    visited = {first}
    while queue:
        cur, forced = queue.pop(0)
        if cur == target:
            return forced
        round_ = by_id.get(cur)
        if round_ is None:
            continue
        for outcome in _outcomes(round_):
            route = route_after_result(
                round_, passed=outcome != "fail", percent=_simulated_percent(round_, outcome)
            )
            nxt = route.next_round_id if route.kind == "advance" else None
            if nxt and nxt in by_id and nxt not in visited:
                visited.add(nxt)
                queue.append((nxt, {**forced, cur: outcome}))
    return None


def build_scenarios(rounds: list[dict[str, Any]],
                    auto_advance: bool = True) -> list[dict[str, Any]]:
    """One synthetic candidate per branch, plus the all-pass path. Pure."""
    if not rounds:
        return []
    by_id = {str(r["id"]): r for r in rounds}
    first = str(min(rounds, key=lambda r: r["position"])["id"])
    plans: list[tuple[str, dict[str, str]]] = [("passes every round", {})]
    for r in sorted(rounds, key=lambda x: x["position"]):
        rid = str(r["id"])
        prefix = _shortest_prefix(by_id, first, rid)
        if prefix is None:
            continue  # unreachable: reported as an error elsewhere
        for outcome in _outcomes(r):
            if outcome == "pass" and not prefix and rid == first:
                continue  # that is the all-pass path
            words = {"pass": "passes", "fast_track": "fast-tracks on",
                     "fail": "falls below the threshold on"}[outcome]
            plans.append((f"{words} {r.get('title')}", {**prefix, rid: outcome}))

    scenarios: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    for label, forced in plans:
        steps, end = _walk(by_id, first, forced, auto_advance)
        key = json.dumps([(s.get("round_id"), s.get("outcome")) for s in steps])
        if key in seen_paths:
            continue
        seen_paths.add(key)
        n = len(scenarios) + 1
        scenarios.append({
            "id": f"SIM-{n:03d}",
            "candidate": f"Simulated candidate {n}",
            "description": label,
            "end": end,
            "steps": steps,
        })
        if len(scenarios) >= MAX_SCENARIOS:
            break
    return scenarios


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------
def _round_checks(
    checks: _Checks,
    rounds: list[dict[str, Any]],
    criteria: dict[str, list[dict[str, Any]]],
    readiness: dict[str, dict[str, Any]],
    kits: set[str],
    stage_settings: dict[str | None, dict[str, Any]],
    *,
    interviewers: int,
    auto_advance: bool,
    task_configs: set[str] | None = None,
) -> None:
    task_configs = task_configs or set()
    for r in rounds:
        rid = str(r["id"])
        kind = r["kind"]
        if kind not in ROUND_KINDS:
            checks.error(f"Unknown round type {kind!r}.", rid)
            continue
        if kind in EXAM_BACKED_KINDS:
            if not r.get("exam_round_id"):
                checks.error(f"A {kind} round needs questions before it can run.", rid)
            else:
                problem = exam_round_problem(readiness.get(str(r["exam_round_id"])))
                if problem is not None:
                    checks.error(
                        f"The attached exam round {problem}, so candidates' links would not "
                        "open.", rid,
                    )
        if kind not in HUMAN_EVALUATED_KINDS and r.get("pass_threshold") is None:
            checks.error("Needs an advance threshold — without one nobody can pass it.", rid)
        if kind in AI_GRADED_KINDS and not criteria.get(rid):
            checks.error("An AI interview must assess at least one competency.", rid)
        if kind in TASK_KINDS and rid not in task_configs:
            checks.error(f"A {kind} round needs a brief and its items before it can run.", rid)
        if kind in HUMAN_EVALUATED_KINDS:
            if not criteria.get(rid):
                checks.warn(
                    "No evaluation criteria: a reviewer can pass or hold, but interviewers "
                    "cannot be assigned scorecards for this round.", rid,
                )
            if rid not in kits:
                checks.warn(
                    "No interview kit: interviewers will see the rubric but no guidance.", rid
                )
            if interviewers == 0:
                checks.warn(
                    "Your company has no interviewer accounts, so HR managers will have to "
                    "interview for this round.", rid,
                )
        threshold = r.get("pass_threshold")
        if threshold is not None and kind not in HUMAN_EVALUATED_KINDS:
            if float(threshold) <= 0:
                checks.warn("An advance threshold of 0% passes everyone.", rid)
            elif float(threshold) >= 100:
                checks.warn("An advance threshold of 100% passes only a perfect score.", rid)
        if (
            r.get("on_fast_track_next_round_id")
            and r.get("on_fast_track_next_round_id") == r.get("on_pass_next_round_id")
        ):
            checks.warn("The fast-track goes where the pass branch already goes.", rid)
        if (r.get("on_fail_next_round_id") or r.get("on_fast_track_next_round_id")) and not auto_advance:
            checks.warn(
                "Branches are only followed when rounds advance automatically, and this "
                "workflow has that switched off.", rid,
            )
        stage = stage_settings.get(rid) or {}
        if stage.get("sla_hours") and not stage.get("owner_user_id"):
            checks.warn("This stage has an SLA but no owner to answer for it.", rid)
        if int(r.get("deadline_days") or 0) > 60:
            checks.warn(f"A {r['deadline_days']}-day deadline is unusually long.", rid)


def _structural_checks(checks: _Checks, rounds: list[dict[str, Any]]) -> None:
    if not rounds:
        checks.error("A workflow needs at least one round.")
        return
    if len(rounds) > MAX_ROUNDS:
        checks.error(f"A workflow cannot have more than {MAX_ROUNDS} rounds.")
    by_title = {r.get("title"): str(r["id"]) for r in rounds}
    for message in branch_errors(rounds):
        title, _, rest = message.partition(": ")
        checks.error(rest or message, by_title.get(title))
    cycles, unreachable = graph_problems(rounds)
    for loop in cycles:
        checks.error(f"The rounds form a loop — a candidate would never finish: {loop}.")
    for title in unreachable:
        checks.error(
            "Unreachable: no branch from the first round ever gets here.", by_title.get(title)
        )


# ---------------------------------------------------------------------------
# Running one
# ---------------------------------------------------------------------------
async def _context(
    db: AsyncSession, *, company_id: uuid.UUID, workflow_id: uuid.UUID
) -> dict[str, Any]:
    wf = (
        await db.execute(
            text(
                "SELECT id, requisition_id, version, status, review_status, auto_advance_rounds"
                "  FROM workflows WHERE id = :i AND company_id = :c AND deleted_at IS NULL"
            ),
            {"i": workflow_id, "c": company_id},
        )
    ).mappings().first()
    if wf is None:
        raise SimulationError(404, "Workflow not found.")
    rounds = await load_rounds(db, workflow_id)
    round_ids = [r["id"] for r in rounds]
    criteria = await load_criteria(db, round_ids)
    readiness = await exam_round_readiness(
        db, [r["exam_round_id"] for r in rounds if r["kind"] in EXAM_BACKED_KINDS]
    )
    kits = {
        str(x) for x in (
            await db.execute(
                text("SELECT round_id FROM interview_kits WHERE round_id = ANY(:ids)"),
                {"ids": round_ids},
            )
        ).scalars().all()
    } if round_ids else set()
    # PH4-D4: which task rounds (job_simulation/portfolio) already have a
    # brief and items configured — the same shape validate() checks at publish.
    task_configs = {
        str(x) for x in (
            await db.execute(
                text("SELECT round_id FROM round_tasks WHERE round_id = ANY(:ids)"),
                {"ids": round_ids},
            )
        ).scalars().all()
    } if round_ids else set()
    stage_rows = (
        await db.execute(
            text(
                "SELECT round_id, owner_user_id, sla_hours FROM workflow_stage_settings"
                " WHERE workflow_id = :w"
            ),
            {"w": workflow_id},
        )
    ).mappings().all()
    stage_settings = {
        (str(s["round_id"]) if s["round_id"] else None): dict(s) for s in stage_rows
    }
    interviewers = int(
        await db.scalar(
            text(
                "SELECT count(*) FROM users u"
                "  JOIN user_roles ur ON ur.user_id = u.id"
                "  JOIN roles r ON r.id = ur.role_id AND r.name = 'interviewer'"
                " WHERE u.company_id = :c AND u.deleted_at IS NULL AND u.is_active"
            ),
            {"c": company_id},
        )
        or 0
    )
    return {"workflow": dict(wf), "rounds": rounds, "criteria": criteria,
            "readiness": readiness, "kits": kits, "stage_settings": stage_settings,
            "interviewers": interviewers, "task_configs": task_configs}


def evaluate(ctx: dict[str, Any], profile_competencies: list[dict[str, Any]] | None) -> dict[str, Any]:
    """The whole result, from a loaded context. Pure — no database."""
    rounds = ctx["rounds"]
    checks = _Checks()
    _structural_checks(checks, rounds)
    _round_checks(
        checks, rounds, ctx["criteria"], ctx["readiness"], ctx["kits"], ctx["stage_settings"],
        interviewers=ctx["interviewers"], auto_advance=bool(ctx["workflow"]["auto_advance_rounds"]),
        task_configs=ctx.get("task_configs", set()),
    )
    decision = ctx["stage_settings"].get(None) or {}
    if decision.get("sla_hours") and not decision.get("owner_user_id"):
        checks.warn("The final decision has an SLA but no owner to answer for it.")
    if profile_competencies:
        titles = {str(r["id"]): r["title"] for r in rounds}
        _rows, coverage_warnings = build_coverage(profile_competencies, ctx["criteria"], titles)
        for message in coverage_warnings:
            checks.warn(message)

    auto_advance = bool(ctx["workflow"]["auto_advance_rounds"])
    if not auto_advance and rounds:
        checks.warn(
            "Rounds do not advance automatically, so nobody moves on by themselves: every "
            "candidate stops after each round until a person moves them. The simulated "
            "candidates below stop where real ones would."
        )
    scenarios = build_scenarios(rounds, auto_advance)
    for sc in scenarios:
        if sc["end"] == "error":
            checks.error(
                f"{sc['id']} ({sc['description']}) could not finish: "
                f"{sc['steps'][-1].get('error')}."
            )

    per_round: dict[str | None, list[dict[str, Any]]] = {}
    for f in checks.findings:
        per_round.setdefault(f.round_id, []).append(f.as_dict())
    errors = sum(1 for f in checks.findings if f.severity == ERROR)
    warnings = sum(1 for f in checks.findings if f.severity == WARNING)
    status = "failed" if errors else ("warnings" if warnings else "passed")

    def state(rid: str) -> str:
        sev = {f["severity"] for f in per_round.get(rid, [])}
        return "error" if ERROR in sev else ("warning" if WARNING in sev else "ok")

    return {
        "status": status,
        "errors": errors,
        "warnings": warnings,
        "rounds": [
            {
                "round_id": str(r["id"]),
                "title": r["title"],
                "kind": r["kind"],
                "position": r["position"],
                "state": state(str(r["id"])),
                "findings": per_round.get(str(r["id"]), []),
                "branches": [
                    {"branch": b, "round_id": t} for b, t in round_edges(r)
                ],
            }
            for r in sorted(rounds, key=lambda x: x["position"])
        ],
        "workflow_findings": per_round.get(None, []),
        "scenarios": scenarios,
    }


async def simulate(
    db: AsyncSession,
    *,
    company_id: uuid.UUID,
    workflow_id: uuid.UUID,
    actor: uuid.UUID,
    profile_competencies: list[dict[str, Any]] | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> dict[str, Any]:
    """Dry-run a version and record the result. Caller commits.

    Writes the result row and one audit row — nothing else, ever.
    """
    ctx = await _context(db, company_id=company_id, workflow_id=workflow_id)
    result = evaluate(ctx, profile_competencies)
    fingerprint = await workflow_fingerprint(db, workflow_id)
    sim_id = uuid.uuid4()
    now = datetime.now(tz=UTC)
    await db.execute(
        text(
            "INSERT INTO workflow_simulations (id, company_id, workflow_id, run_by_user_id,"
            " fingerprint, status, errors, warnings, scenarios, result, created_at)"
            " VALUES (:i, :c, :w, :u, :fp, :s, :e, :wn, :sc, CAST(:r AS jsonb), :n)"
        ),
        {"i": sim_id, "c": company_id, "w": workflow_id, "u": actor, "fp": fingerprint,
         "s": result["status"], "e": result["errors"], "wn": result["warnings"],
         "sc": len(result["scenarios"]), "r": json.dumps(result, default=str), "n": now},
    )
    db.add(
        AuditLog(
            actor_id=actor,
            actor_type="user",
            action="workflow.simulated",
            resource_type="workflow",
            resource_id=workflow_id,
            details={
                "company_id": str(company_id),
                "version": ctx["workflow"]["version"],
                "simulation_id": str(sim_id),
                "status": result["status"],
                "errors": result["errors"],
                "warnings": result["warnings"],
                "scenarios": len(result["scenarios"]),
                "fingerprint": fingerprint,
            },
            ip_address=ip_address,
            user_agent=user_agent,
            event_ts=now,
        )
    )
    log.info("workflow.simulated", workflow_id=str(workflow_id), status=result["status"],
             errors=result["errors"], warnings=result["warnings"],
             scenarios=len(result["scenarios"]))
    return {
        "simulation_id": str(sim_id),
        "workflow_id": str(workflow_id),
        "version": ctx["workflow"]["version"],
        "fingerprint": fingerprint,
        "created_at": now.isoformat(),
        "stale": False,
        **result,
    }


async def latest_simulation(
    db: AsyncSession, *, company_id: uuid.UUID, workflow_id: uuid.UUID
) -> dict[str, Any] | None:
    """The most recent result for a version, marked stale if the version changed since."""
    row = (
        await db.execute(
            text(
                "SELECT s.id, s.fingerprint, s.status, s.errors, s.warnings, s.result,"
                "       s.created_at, s.run_by_user_id, u.full_name AS run_by_name,"
                "       w.version"
                "  FROM workflow_simulations s"
                "  JOIN workflows w ON w.id = s.workflow_id"
                "  LEFT JOIN users u ON u.id = s.run_by_user_id"
                " WHERE s.workflow_id = :w AND s.company_id = :c"
                " ORDER BY s.created_at DESC LIMIT 1"
            ),
            {"w": workflow_id, "c": company_id},
        )
    ).mappings().first()
    if row is None:
        return None
    current = await workflow_fingerprint(db, workflow_id)
    result = row["result"] if isinstance(row["result"], dict) else json.loads(row["result"])
    return {
        "simulation_id": str(row["id"]),
        "workflow_id": str(workflow_id),
        "version": row["version"],
        "fingerprint": row["fingerprint"],
        "created_at": row["created_at"].isoformat(),
        "run_by_name": row["run_by_name"],
        "stale": row["fingerprint"] != current,
        **result,
    }
