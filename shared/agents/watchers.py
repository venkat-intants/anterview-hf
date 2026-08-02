"""Proactive watchers — the agents nobody has to ask.

A copilot only helps someone who thinks to open it. Most of what goes wrong in
a hiring pipeline goes wrong quietly: a candidate sits in "shortlisted" for
three weeks, an exam question nobody can answer silently fails a whole cohort,
a DPDP erasure deadline creeps up. Watchers run on a schedule, look for those,
and raise a notification.

Design constraints, all deliberate:

* **Findings, not actions.** A watcher returns ``WatcherFinding`` objects. It
  has no write authority — same guarantee as the chat agents — so the worst a
  broken watcher can do is send a wrong notification.
* **Mostly deterministic.** Every rule here is SQL plus arithmetic. Watchers
  run unattended across every company, so an LLM in the loop would mean an
  unbounded nightly bill and non-reproducible alerts. A model is used only to
  phrase a finding when one is worth phrasing, and the deterministic body is
  always present as a fallback.
* **Deduplicated.** Each finding carries a ``dedupe_key`` derived from what it
  is about, not from when it fired. Without it, "7 applicants stalled" notifies
  the same HR manager every single night until they act, and they learn to
  ignore the bell.

The detection rules live here so they are unit-testable against plain data; the
SQL that feeds them lives in the service that owns the tables.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

import structlog

from shared.agents.schema import Citation, WatcherFinding

log = structlog.get_logger(__name__)

# An applicant sitting in a non-terminal stage longer than this needs a nudge.
# 10 days is about the point where a candidate assumes they were rejected and
# takes another offer — the cost of the alert is far below the cost of the
# silence.
STALLED_DAYS: int = 10

# A funnel this lossy is usually a process problem, not a candidate-quality
# problem: enough applicants to be meaningful, almost none reaching interview.
FUNNEL_MIN_APPLICANTS: int = 15
FUNNEL_INTERVIEW_RATE_FLOOR: float = 0.10

# A question this many attempts old that almost nobody gets right is more
# likely broken (ambiguous, wrong key, out of syllabus) than genuinely hard.
BAD_QUESTION_MIN_ATTEMPTS: int = 8
BAD_QUESTION_CORRECT_RATE: float = 0.15
# ...and one everybody gets right is not discriminating between candidates.
TRIVIAL_QUESTION_CORRECT_RATE: float = 0.98

# DPDP erasure requests must be actioned inside the statutory window. Warn
# early enough that a human has a working day to act.
ERASURE_WARN_HOURS: int = 48


@dataclass
class StalledApplicant:
    applicant_id: str
    name: str
    stage: str
    days_in_stage: int


@dataclass
class FunnelRow:
    job_id: str
    job_title: str
    applicants: int
    interviewed: int


@dataclass
class QuestionStat:
    exam_id: str
    exam_title: str
    question_id: str
    position: int
    attempts: int
    correct: int


@dataclass
class ErasureRequest:
    request_id: str
    hours_remaining: float


@dataclass
class WatcherInput:
    """Everything the watchers need, gathered by the service in one pass.

    A single dataclass rather than four separate queries per watcher: the
    scheduler runs this for every company, so the DB round-trip count is the
    thing that decides whether nightly execution is cheap or a problem.
    """

    company_id: str
    stalled: list[StalledApplicant] = field(default_factory=list)
    funnels: list[FunnelRow] = field(default_factory=list)
    question_stats: list[QuestionStat] = field(default_factory=list)
    erasure_requests: list[ErasureRequest] = field(default_factory=list)


def watch_stalled_applicants(data: WatcherInput) -> list[WatcherFinding]:
    """Candidates going cold in a non-terminal stage."""
    stalled = [a for a in data.stalled if a.days_in_stage >= STALLED_DAYS]
    if not stalled:
        return []

    stalled.sort(key=lambda a: -a.days_in_stage)
    worst = stalled[0]
    names = ", ".join(a.name for a in stalled[:5])
    more = f" and {len(stalled) - 5} more" if len(stalled) > 5 else ""

    return [
        WatcherFinding(
            watcher="stalled_applicants",
            severity="warning" if len(stalled) < 10 else "critical",
            title=f"{len(stalled)} applicant(s) stalled over {STALLED_DAYS} days",
            body=(
                f"{names}{more} have not moved stage. The longest, {worst.name}, "
                f"has been in '{worst.stage}' for {worst.days_in_stage} days. "
                "Candidates usually assume silence means rejection."
            ),
            link="/hr/pipeline",
            # Keyed on WHO is stalled, so the alert re-fires only when the set
            # changes — not every night until someone acts.
            dedupe_key="stalled:" + ",".join(sorted(a.applicant_id for a in stalled)),
            citations=[
                Citation(
                    kind="applicant",
                    id=a.applicant_id,
                    label=a.name,
                    href=f"/hr/applicants/{a.applicant_id}",
                )
                for a in stalled[:10]
            ],
        )
    ]


def watch_funnel_health(data: WatcherInput) -> list[WatcherFinding]:
    """Roles where applicants arrive but nobody reaches an interview."""
    findings: list[WatcherFinding] = []
    for row in data.funnels:
        if row.applicants < FUNNEL_MIN_APPLICANTS:
            continue  # too few to distinguish a problem from noise
        rate = row.interviewed / row.applicants if row.applicants else 0.0
        if rate > FUNNEL_INTERVIEW_RATE_FLOOR:
            continue
        findings.append(
            WatcherFinding(
                watcher="funnel_health",
                severity="warning",
                title=f"'{row.job_title}' funnel is losing almost everyone",
                body=(
                    f"{row.applicants} applicants, {row.interviewed} interviewed "
                    f"({rate:.0%}). At this volume that usually means the "
                    "screening bar or the job description is off, rather than "
                    "the applicant pool."
                ),
                link="/hr/analytics",
                dedupe_key=f"funnel:{row.job_id}:{row.applicants // 10}",
                citations=[
                    Citation(kind="job", id=row.job_id, label=row.job_title)
                ],
            )
        )
    return findings


def watch_exam_quality(data: WatcherInput) -> list[WatcherFinding]:
    """Questions that are probably broken, or that discriminate nothing."""
    findings: list[WatcherFinding] = []
    for stat in data.question_stats:
        if stat.attempts < BAD_QUESTION_MIN_ATTEMPTS:
            continue
        rate = stat.correct / stat.attempts

        if rate <= BAD_QUESTION_CORRECT_RATE:
            findings.append(
                WatcherFinding(
                    watcher="exam_quality",
                    severity="warning",
                    title=f"Q{stat.position} in '{stat.exam_title}' — almost nobody passes it",
                    body=(
                        f"{stat.correct} of {stat.attempts} attempts correct "
                        f"({rate:.0%}). A question this hard is more often "
                        "ambiguous, mis-keyed, or outside the syllabus than "
                        "genuinely difficult. Worth re-reading before it costs "
                        "you more candidates."
                    ),
                    link=f"/hr/exams/{stat.exam_id}",
                    dedupe_key=f"exam_hard:{stat.question_id}",
                    citations=[
                        Citation(
                            kind="exam",
                            id=stat.exam_id,
                            label=stat.exam_title,
                            href=f"/hr/exams/{stat.exam_id}",
                        )
                    ],
                )
            )
        elif rate >= TRIVIAL_QUESTION_CORRECT_RATE:
            findings.append(
                WatcherFinding(
                    watcher="exam_quality",
                    severity="info",
                    title=f"Q{stat.position} in '{stat.exam_title}' separates nobody",
                    body=(
                        f"{stat.correct} of {stat.attempts} attempts correct "
                        f"({rate:.0%}). It is not distinguishing between "
                        "candidates, so it is costing exam time without "
                        "producing signal."
                    ),
                    link=f"/hr/exams/{stat.exam_id}",
                    dedupe_key=f"exam_trivial:{stat.question_id}",
                    citations=[
                        Citation(
                            kind="exam",
                            id=stat.exam_id,
                            label=stat.exam_title,
                            href=f"/hr/exams/{stat.exam_id}",
                        )
                    ],
                )
            )
    return findings


def watch_dpdp_deadlines(data: WatcherInput) -> list[WatcherFinding]:
    """Erasure requests approaching their statutory deadline."""
    due = [r for r in data.erasure_requests if r.hours_remaining <= ERASURE_WARN_HOURS]
    if not due:
        return []
    soonest = min(r.hours_remaining for r in due)
    return [
        WatcherFinding(
            watcher="dpdp_deadlines",
            # Always critical. A missed erasure deadline is a statutory breach,
            # not a backlog item, and it must not sit at the same visual weight
            # as a stalled candidate.
            severity="critical",
            title=f"{len(due)} DPDP erasure request(s) due within {ERASURE_WARN_HOURS}h",
            body=(
                f"The soonest is due in {soonest:.0f} hours. Erasure requests "
                "carry a statutory deadline under the DPDP Act 2023."
            ),
            link="/platform/dpdp",
            dedupe_key="dpdp:" + ",".join(sorted(r.request_id for r in due)),
            citations=[
                Citation(kind="audit", id=r.request_id, label="Erasure request")
                for r in due[:10]
            ],
        )
    ]


# Registry of every watcher. The scheduler iterates this, so adding a watcher
# is one function plus one entry — no scheduler edit.
WATCHERS: tuple[tuple[str, object], ...] = (
    ("dpdp_deadlines", watch_dpdp_deadlines),
    ("stalled_applicants", watch_stalled_applicants),
    ("funnel_health", watch_funnel_health),
    ("exam_quality", watch_exam_quality),
)

_SEVERITY_ORDER: dict[str, int] = {"critical": 0, "warning": 1, "info": 2}


def run_watchers(data: WatcherInput) -> list[WatcherFinding]:
    """Run every watcher over one company's data. Pure and never raises.

    A watcher that throws is skipped with a log line rather than taking the
    nightly sweep down — one broken rule must not cost every company every
    other alert.
    """
    findings: list[WatcherFinding] = []
    for name, watcher in WATCHERS:
        try:
            findings.extend(watcher(data))  # type: ignore[operator]  # tuple-typed registry
        except Exception as exc:  # noqa: BLE001 — see docstring
            log.warning(
                "agents.watcher.failed",
                watcher=name,
                company_id=data.company_id,
                error=type(exc).__name__,
            )

    findings.sort(key=lambda f: _SEVERITY_ORDER[f.severity])
    log.info(
        "agents.watchers.complete",
        company_id=data.company_id,
        findings=len(findings),
        critical=sum(1 for f in findings if f.severity == "critical"),
    )
    return findings


def digest(findings: list[WatcherFinding], *, when: datetime | None = None) -> str:
    """Render findings as a plain-text digest for email or a console banner."""
    stamp = (when or datetime.now(tz=UTC)).strftime("%d %b %Y")
    if not findings:
        return f"Pipeline check — {stamp}\n\nNothing needs attention."

    lines = [f"Pipeline check — {stamp}", ""]
    icon = {"critical": "!!", "warning": " !", "info": "  "}
    for finding in findings:
        lines.append(f"{icon[finding.severity]} {finding.title}")
        lines.append(f"     {finding.body}")
        lines.append("")
    return "\n".join(lines).rstrip()
